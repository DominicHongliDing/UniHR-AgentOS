from tempfile import TemporaryDirectory
from pathlib import Path

from app.domains.conversations.repository import ConversationRepository
from app.domains.resources.workspace import ResourceWorkspace


def test_resource_workspace_releases_sqlite_file_before_workspace_cleanup():
    with TemporaryDirectory(prefix="unihr-resource-workspace-") as temp_name:
        database_path = Path(temp_name) / "sandbox.db"
        repository = ConversationRepository(database_path)
        ResourceWorkspace(repository)
        assert database_path.is_file()

    assert not Path(temp_name).exists()
