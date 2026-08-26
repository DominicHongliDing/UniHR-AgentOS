from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4


class LegacyOfficeConversionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyOfficeConversion:
    path: Path
    extension: str
    mime_type: str
    engine: str
    application_version: str = "unknown"


ConversionRunner = Callable[[Path, Path, str, int], dict[str, object]]


class LegacyOfficeConverter:
    """Convert legacy binary Office files into non-macro OOXML copies."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        timeout_seconds: int = 90,
        runner: ConversionRunner | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.runner = runner or self._run_office

    def convert(self, source: str | Path) -> LegacyOfficeConversion:
        source_path = Path(source).resolve()
        if source_path.suffix.casefold() == ".doc":
            target = self.output_dir / f"converted_{uuid4().hex}.docx"
            kind = "word"
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif source_path.suffix.casefold() == ".xls":
            target = self.output_dir / f"converted_{uuid4().hex}.xlsx"
            kind = "excel"
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            raise LegacyOfficeConversionError("unsupported_legacy_extension")
        try:
            metadata = self.runner(source_path, target, kind, self.timeout_seconds)
        except (OSError, subprocess.SubprocessError, ValueError, LegacyOfficeConversionError) as exc:
            target.unlink(missing_ok=True)
            raise LegacyOfficeConversionError("legacy_office_conversion_failed") from exc
        if not target.is_file() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise LegacyOfficeConversionError("legacy_office_conversion_output_missing")
        return LegacyOfficeConversion(
            path=target,
            extension=target.suffix.casefold(),
            mime_type=mime,
            engine=f"Microsoft {kind.title()} COM",
            application_version=str(metadata.get("application_version") or "unknown"),
        )

    @staticmethod
    def _run_office(source: Path, target: Path, kind: str, timeout_seconds: int) -> dict[str, object]:
        source_literal = str(source).replace("'", "''")
        target_literal = str(target).replace("'", "''")
        if kind == "word":
            body = f"""
$app = New-Object -ComObject Word.Application
$app.Visible = $false; $app.DisplayAlerts = 0; $app.AutomationSecurity = 3
$document = $app.Documents.Open('{source_literal}', $false, $true)
$document.SaveAs2('{target_literal}', 16)
[pscustomobject]@{{ application_version = [string]$app.Version }} | ConvertTo-Json -Compress
"""
            close = "$document.Close(0)"
        else:
            body = f"""
$app = New-Object -ComObject Excel.Application
$app.Visible = $false; $app.DisplayAlerts = $false; $app.AskToUpdateLinks = $false; $app.AutomationSecurity = 3
$document = $app.Workbooks.Open('{source_literal}', 0, $true)
$document.SaveAs('{target_literal}', 51)
[pscustomobject]@{{ application_version = [string]$app.Version }} | ConvertTo-Json -Compress
"""
            close = "$document.Close($false)"
        script = f"""
$ErrorActionPreference = 'Stop'
$app = $null; $document = $null
try {{ {body} }} finally {{
  if ($document -ne $null) {{ {close}; [void][Runtime.InteropServices.Marshal]::ReleaseComObject($document) }}
  if ($app -ne $null) {{ $app.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($app) }}
  [GC]::Collect(); [GC]::WaitForPendingFinalizers()
}}
"""
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=timeout_seconds, check=False,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            raise LegacyOfficeConversionError("legacy_office_automation_failed")
        try:
            return json.loads((completed.stdout.strip().splitlines() or ["{}"])[-1])
        except json.JSONDecodeError as exc:
            raise LegacyOfficeConversionError("legacy_office_automation_output_invalid") from exc
