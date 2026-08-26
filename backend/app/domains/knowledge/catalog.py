from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import (
    CatalogSnapshot,
    ChunkProfile,
    IngestionDisposition,
    SensitivityLevel,
    SourceRecord,
)


_VECTOR_EXTENSIONS = {".pdf", ".docx", ".doc", ".md", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
_TABLE_EXTENSIONS = {".xlsx", ".xls", ".csv", ".tsv"}
_SKILL_EXTENSIONS = {".py", ".ps1", ".yaml", ".yml"}
_MEDIA_EXTENSIONS = {".mp4", ".mov"}

_COLLECTION_DEFAULTS: dict[str, tuple[SensitivityLevel, IngestionDisposition]] = {
    "calculate-annual-housing-fund": (
        SensitivityLevel.HR_CONFIDENTIAL,
        IngestionDisposition.STRUCTURED_TABLE,
    ),
    "department-weekly-minutes": (
        SensitivityLevel.INTERNAL,
        IngestionDisposition.SKILL_ASSET,
    ),
    "hr-audit-material-finder": (
        SensitivityLevel.INTERNAL,
        IngestionDisposition.SKILL_ASSET,
    ),
    "medical-faculty-contact-finder": (
        SensitivityLevel.HR_CONFIDENTIAL,
        IngestionDisposition.CASE_ONLY,
    ),
    "official-document-skill": (
        SensitivityLevel.INTERNAL,
        IngestionDisposition.SKILL_ASSET,
    ),
    "postdoc-eval-form-builder": (
        SensitivityLevel.STRICTLY_RESTRICTED,
        IngestionDisposition.CASE_ONLY,
    ),
    "postdoc-management": (
        SensitivityLevel.HR_CONFIDENTIAL,
        IngestionDisposition.CASE_ONLY,
    ),
    "salary-letter-analysis": (
        SensitivityLevel.STRICTLY_RESTRICTED,
        IngestionDisposition.CASE_ONLY,
    ),
}

_POLICY_HINTS = ("政策", "办法", "规定", "细则", "通知", "方案", "指南", "规范")
_PERSON_HINTS = (
    "名单",
    "人员",
    "简历",
    "申请材料",
    "工资函件",
    "在职",
    "离职",
    "身份证",
    "护照",
)


class SourceCatalog:
    """Build a local, non-ingesting catalog for review and UI prototyping.

    It never opens file contents, calls an embedding model, or contacts a vector
    database. File names are used only for local classification and are not
    returned in the snapshot.
    """

    def dry_run(self, root: str | Path) -> CatalogSnapshot:
        root_path = Path(root)
        if not root_path.exists():
            return CatalogSnapshot(root_exists=False, file_count=0, total_bytes=0)

        records_with_paths = self.records_with_paths(root_path)
        records = [record for _, record in records_with_paths]
        total_bytes = sum(record.byte_size for record in records)

        return CatalogSnapshot(
            root_exists=True,
            file_count=len(records),
            total_bytes=total_bytes,
            records=records,
        )

    def records_with_paths(self, root: str | Path) -> list[tuple[Path, SourceRecord]]:
        """Resolve local paths for controlled ingestion without exposing them via API."""
        root_path = Path(root)
        if not root_path.exists():
            return []
        result: list[tuple[Path, SourceRecord]] = []
        for path in sorted(item for item in root_path.rglob("*") if item.is_file()):
            relative = path.relative_to(root_path)
            collection = relative.parts[0] if len(relative.parts) > 1 else "unassigned"
            result.append((path, self._classify(path, relative, collection, path.stat().st_size)))
        return result

    def _classify(
        self,
        path: Path,
        relative: Path,
        collection: str,
        byte_size: int,
    ) -> SourceRecord:
        extension = path.suffix.lower() or "[none]"
        sensitivity, default_disposition = _COLLECTION_DEFAULTS.get(
            collection,
            (SensitivityLevel.INTERNAL, IngestionDisposition.QUARANTINE),
        )
        local_text = str(relative)
        reasons: list[str] = [f"collection_default:{collection}"]
        needs_confirmation = False

        if extension in {".docm", ".xlsm", ".pptm"}:
            disposition = IngestionDisposition.QUARANTINE
            profile = ChunkProfile.UNSUPPORTED
            needs_confirmation = True
            reasons.append("macro_enabled_office_file")
        elif extension in _MEDIA_EXTENSIONS:
            disposition = IngestionDisposition.SKILL_ASSET
            profile = ChunkProfile.SKILL_RESOURCE
            needs_confirmation = True
            reasons.append("media_requires_approved_transcript")
        elif extension in _SKILL_EXTENSIONS or collection == "unassigned":
            disposition = IngestionDisposition.SKILL_ASSET
            profile = ChunkProfile.SKILL_RESOURCE
            reasons.append("runtime_or_skill_resource")
        elif extension in _TABLE_EXTENSIONS:
            profile = (
                ChunkProfile.DELIMITED_TABLE
                if extension in {".csv", ".tsv"}
                else ChunkProfile.STRUCTURED_WORKBOOK
            )
            disposition = (
                IngestionDisposition.CASE_ONLY
                if sensitivity == SensitivityLevel.STRICTLY_RESTRICTED
                or any(hint in local_text for hint in _PERSON_HINTS)
                else IngestionDisposition.STRUCTURED_TABLE
            )
            reasons.append("table_requires_schema_and_row_citations")
        elif extension == ".html":
            disposition = default_disposition
            profile = ChunkProfile.WEB_SNAPSHOT
            reasons.append("html_requires_sanitization_and_canonical_url")
        elif extension == ".pptx":
            disposition = default_disposition
            profile = ChunkProfile.PRESENTATION
            reasons.append("slide_level_citations")
        elif extension in _VECTOR_EXTENSIONS:
            profile = (
                ChunkProfile.PLAIN_TEXT
                if extension in {".md", ".txt"}
                else ChunkProfile.SCANNED_DOCUMENT
                if extension in {".png", ".jpg", ".jpeg", ".webp"}
                else ChunkProfile.POLICY_DOCUMENT
            )
            if any(hint in local_text for hint in _PERSON_HINTS):
                disposition = IngestionDisposition.CASE_ONLY
                needs_confirmation = True
                reasons.append("person_specific_material")
            elif any(hint in local_text for hint in _POLICY_HINTS):
                disposition = IngestionDisposition.VECTOR
                reasons.append("policy_like_material")
            else:
                disposition = default_disposition
        else:
            disposition = IngestionDisposition.QUARANTINE
            profile = ChunkProfile.UNSUPPORTED
            needs_confirmation = True
            reasons.append("unsupported_extension")

        if extension in {".doc", ".xls"}:
            needs_confirmation = True
            reasons.append("legacy_office_requires_isolated_conversion")

        return SourceRecord(
            source_id=self._source_id(relative, byte_size),
            collection=collection,
            extension=extension,
            byte_size=byte_size,
            sensitivity=sensitivity,
            disposition=disposition,
            chunk_profile=profile,
            requires_human_confirmation=needs_confirmation,
            reasons=reasons,
        )

    @staticmethod
    def _source_id(relative: Path, byte_size: int) -> str:
        digest = hashlib.sha256(
            f"{relative.as_posix()}:{byte_size}".encode("utf-8")
        ).hexdigest()
        return f"src_{digest[:16]}"
