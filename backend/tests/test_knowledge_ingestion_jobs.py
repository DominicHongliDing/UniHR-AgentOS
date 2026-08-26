from app.domains.knowledge.jobs import KnowledgeIngestionJobRepository


def test_ingestion_job_persists_progress_and_terminal_result(tmp_path):
    repository = KnowledgeIngestionJobRepository(tmp_path / "jobs.db")
    job = repository.create(tenant_id="tenant-a", actor_id="admin-a")

    repository.progress(
        job.job_id, total=12, processed=4,
        current_file="policy/scanned.pdf", stage="extracting",
    )
    running = repository.get(job.job_id, tenant_id="tenant-a")
    assert running.status == "running"
    assert running.total_files == 12
    assert running.processed_files == 4
    assert running.current_file == "policy/scanned.pdf"

    repository.complete(job.job_id, {"indexed_sources": 8, "failed_sources": 1})
    completed = repository.latest(tenant_id="tenant-a")
    assert completed is not None
    assert completed.status == "completed"
    assert completed.result["indexed_sources"] == 8


def test_ingestion_jobs_are_tenant_isolated(tmp_path):
    repository = KnowledgeIngestionJobRepository(tmp_path / "jobs.db")
    job = repository.create(tenant_id="tenant-a", actor_id="admin-a")

    try:
        repository.get(job.job_id, tenant_id="tenant-b")
    except KeyError as exc:
        assert exc.args[0] == "knowledge_ingestion_job_not_found"
    else:
        raise AssertionError("cross-tenant job access must be denied")
