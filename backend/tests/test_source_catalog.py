from pathlib import Path

from app.domains.knowledge import (
    ChunkProfile,
    IngestionDisposition,
    SensitivityLevel,
    SourceCatalog,
)


def test_dry_run_never_exposes_file_names_or_paths(tmp_path: Path):
    collection = tmp_path / "postdoc-eval-form-builder"
    collection.mkdir()
    sensitive_name = "candidate-private-resume.pdf"
    (collection / sensitive_name).write_bytes(b"not opened by dry run")

    snapshot = SourceCatalog().dry_run(tmp_path)

    assert snapshot.file_count == 1
    encoded = snapshot.model_dump_json()
    assert sensitive_name not in encoded
    assert str(tmp_path) not in encoded
    record = snapshot.records[0]
    assert record.sensitivity == SensitivityLevel.STRICTLY_RESTRICTED
    assert record.disposition == IngestionDisposition.CASE_ONLY
    assert record.chunk_profile == ChunkProfile.POLICY_DOCUMENT


def test_policy_can_be_proposed_for_vector_but_person_table_stays_case_only(
    tmp_path: Path,
):
    collection = tmp_path / "postdoc-management"
    collection.mkdir()
    (collection / "2026政策办法.pdf").write_bytes(b"placeholder")
    (collection / "在职人员名单.xlsx").write_bytes(b"placeholder")

    snapshot = SourceCatalog().dry_run(tmp_path)
    dispositions = {record.extension: record.disposition for record in snapshot.records}

    assert dispositions[".pdf"] == IngestionDisposition.VECTOR
    assert dispositions[".xlsx"] == IngestionDisposition.CASE_ONLY


def test_macro_enabled_file_is_quarantined(tmp_path: Path):
    collection = tmp_path / "postdoc-eval-form-builder"
    collection.mkdir()
    (collection / "evaluation.docm").write_bytes(b"placeholder")

    record = SourceCatalog().dry_run(tmp_path).records[0]

    assert record.disposition == IngestionDisposition.QUARANTINE
    assert record.requires_human_confirmation is True


def test_missing_root_returns_empty_snapshot(tmp_path: Path):
    snapshot = SourceCatalog().dry_run(tmp_path / "missing")

    assert snapshot.root_exists is False
    assert snapshot.file_count == 0
    assert snapshot.total_bytes == 0
