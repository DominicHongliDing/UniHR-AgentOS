from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SensitivityLevel(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    HR_CONFIDENTIAL = "hr_confidential"
    STRICTLY_RESTRICTED = "strictly_restricted"


class IngestionDisposition(StrEnum):
    VECTOR = "vector"
    STRUCTURED_TABLE = "structured_table"
    SKILL_ASSET = "skill_asset"
    CASE_ONLY = "case_only"
    QUARANTINE = "quarantine"


class ChunkProfile(StrEnum):
    POLICY_DOCUMENT = "policy_document"
    SCANNED_DOCUMENT = "scanned_document"
    STRUCTURED_WORKBOOK = "structured_workbook"
    DELIMITED_TABLE = "delimited_table"
    PLAIN_TEXT = "plain_text"
    WEB_SNAPSHOT = "web_snapshot"
    PRESENTATION = "presentation"
    SKILL_RESOURCE = "skill_resource"
    UNSUPPORTED = "unsupported"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"


class AclSubjectType(StrEnum):
    ORGANIZATION = "organization"
    ROLE = "role"
    USER = "user"


class KnowledgeAccessContext(BaseModel):
    tenant_id: str
    user_id: str
    role: str


class SourceVersionRecord(BaseModel):
    version_id: str
    source_id: str
    title: str
    collection: str
    checksum: str
    source_version: str | None = None
    publication_date: str | None = None
    effective_date: str | None = None
    expiry_date: str | None = None
    policy_status: str = "unknown"
    authority_level: str = "unknown"
    chunk_count: int = 0
    lifecycle_status: PublicationStatus = PublicationStatus.DRAFT
    publication_id: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)
    created_at: str = ""
    published_at: str | None = None
    ingestion_status: str = "ready"
    quality_score: int = 100
    quality_review_status: str = "not_required"
    quality_report: dict[str, object] = Field(default_factory=dict)


class SourceRecord(BaseModel):
    """A privacy-preserving dry-run record.

    The original file name and absolute path are deliberately excluded. Runtime
    ingestion may resolve them inside a controlled vault by ``source_id``.
    """

    source_id: str
    collection: str
    extension: str
    byte_size: int = Field(ge=0)
    sensitivity: SensitivityLevel
    disposition: IngestionDisposition
    chunk_profile: ChunkProfile
    requires_human_confirmation: bool = False
    reasons: list[str] = Field(default_factory=list)


class CatalogSnapshot(BaseModel):
    root_exists: bool
    file_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    records: list[SourceRecord] = Field(default_factory=list)

    def count_by_disposition(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for record in self.records:
            key = record.disposition.value
            result[key] = result.get(key, 0) + 1
        return result

    def count_by_collection(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for record in self.records:
            result[record.collection] = result.get(record.collection, 0) + 1
        return result
