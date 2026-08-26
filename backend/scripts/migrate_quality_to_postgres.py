from __future__ import annotations

import json

from app.domains.quality_operations.migration import migrate_sqlite_quality_evidence
from app.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    if settings.database_path is None:
        raise RuntimeError("UNIHR_DATABASE_PATH is required")
    if not settings.quality_database_dsn:
        raise RuntimeError("UNIHR_QUALITY_POSTGRES_DSN is required")
    result = migrate_sqlite_quality_evidence(
        settings.database_path, settings.quality_database_dsn,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
