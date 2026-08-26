from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.observability import emit_tool_progress


class WordVerificationReport(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)
    issues: list[str] = Field(default_factory=list)
    preview_files: list[str] = Field(default_factory=list)


class WordAutomationVerifier:
    """Open a DOCX with desktop Word and export a real PDF preview."""

    def __init__(
        self,
        preview_dir: str | Path,
        *,
        word_executable: str | Path = r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
        timeout_seconds: int = 90,
    ) -> None:
        self.preview_dir = Path(preview_dir)
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.word_executable = Path(word_executable)
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return self.word_executable.is_file()

    def verify(self, document_path: str | Path) -> WordVerificationReport:
        document = Path(document_path).resolve()
        checks = {"word_automation_available": self.available}
        metrics: dict[str, int | float | str | bool] = {"render_engine": "Microsoft Word COM"}
        if not self.available:
            emit_tool_progress(
                "tool.document.word.failed",
                "当前环境无法调用桌面 Word 进行重开与渲染",
                status="failed",
                error_code="word_automation_unavailable",
            )
            return WordVerificationReport(
                passed=False, checks=checks, metrics=metrics, issues=["word_automation_unavailable"],
            )
        preview = self.preview_dir / f"document-preview-{uuid4().hex}.pdf"
        emit_tool_progress(
            "tool.document.word.started",
            "正在调用桌面 Word 重开文档并导出 PDF 预览",
        )
        try:
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-Command", self._powershell_script(document, preview),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            emit_tool_progress(
                "tool.document.word.failed",
                "桌面 Word 重开与预览导出超时",
                status="failed",
                error_code="word_automation_timeout",
            )
            return WordVerificationReport(
                passed=False,
                checks={**checks, "word_open_and_export_completed": False},
                metrics=metrics,
                issues=["word_automation_timeout"],
            )
        checks["word_open_and_export_completed"] = completed.returncode == 0
        issues: list[str] = []
        if completed.returncode != 0:
            emit_tool_progress(
                "tool.document.word.failed",
                "桌面 Word 未能完成文档重开与预览导出",
                status="failed",
                error_code="word_automation_failed",
            )
            issues.append("word_automation_failed")
            metrics["automation_error"] = (completed.stderr or completed.stdout)[-1_000:]
            preview.unlink(missing_ok=True)
            return WordVerificationReport(
                passed=False, checks=checks, metrics=metrics, issues=issues,
            )
        try:
            automation = json.loads((completed.stdout.strip().splitlines() or ["{}"])[-1])
        except json.JSONDecodeError:
            automation = {}
            issues.append("word_automation_output_invalid")
        metrics["word_version"] = str(automation.get("word_version") or "unknown")
        metrics["word_page_count"] = int(automation.get("page_count") or 0)
        emit_tool_progress(
            "tool.document.word.completed",
            f"桌面 Word 已完成重开与导出，共 {metrics['word_page_count']} 页",
            status="completed",
            engine="Microsoft Word COM",
            word_version=metrics["word_version"],
            word_page_count=metrics["word_page_count"],
        )
        emit_tool_progress(
            "tool.document.render.started",
            "正在检查 PDF 预览页是否完整且含有内容",
        )
        checks["rendered_pdf_exists"] = preview.is_file() and preview.stat().st_size > 0
        if checks["rendered_pdf_exists"]:
            try:
                reader = PdfReader(preview)
                page_count = len(reader.pages)
                content_pages = sum(
                    1 for page in reader.pages
                    if page.get_contents() is not None and len(page.get_contents().get_data()) > 0
                )
                checks["rendered_pdf_has_pages"] = page_count > 0
                checks["rendered_pages_have_content"] = page_count > 0 and content_pages == page_count
                metrics["rendered_page_count"] = page_count
                metrics["rendered_content_page_count"] = content_pages
            except (OSError, ValueError, KeyError) as exc:
                checks["rendered_pdf_has_pages"] = False
                checks["rendered_pages_have_content"] = False
                issues.append(f"rendered_pdf_invalid:{type(exc).__name__}")
        else:
            checks["rendered_pdf_has_pages"] = False
            checks["rendered_pages_have_content"] = False
            issues.append("rendered_pdf_missing")
        passed = all(checks.values())
        emit_tool_progress(
            "tool.document.render.completed",
            (
                f"Word 预览检查完成，共 {int(metrics.get('rendered_page_count') or 0)} 页"
                if checks.get("rendered_pages_have_content")
                else "Word 预览检查发现问题"
            ),
            status="completed" if passed else "failed",
            rendered_page_count=metrics.get("rendered_page_count"),
            rendered_content_page_count=metrics.get("rendered_content_page_count"),
        )
        return WordVerificationReport(
            passed=passed,
            checks=checks,
            metrics=metrics,
            issues=list(dict.fromkeys(issues)),
            preview_files=[str(preview)] if preview.is_file() else [],
        )

    @staticmethod
    def _powershell_script(document: Path, preview: Path) -> str:
        document_literal = str(document).replace("'", "''")
        preview_literal = str(preview).replace("'", "''")
        return f"""
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {{
  $word = New-Object -ComObject Word.Application
  $word.Visible = $false
  $word.DisplayAlerts = 0
  $word.AutomationSecurity = 3
  $doc = $word.Documents.Open('{document_literal}', $false, $true)
  $pageCount = $doc.ComputeStatistics(2)
  $doc.ExportAsFixedFormat('{preview_literal}', 17, $false, 0, 0, 1, 2147483647, 0, $true, $true, 0, $true, $true, $false)
  [pscustomobject]@{{ word_version = [string]$word.Version; page_count = [int]$pageCount; rendered = (Test-Path -LiteralPath '{preview_literal}') }} | ConvertTo-Json -Compress
}} finally {{
  if ($doc -ne $null) {{ $doc.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($doc) }}
  if ($word -ne $null) {{ $word.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($word) }}
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}}
"""
