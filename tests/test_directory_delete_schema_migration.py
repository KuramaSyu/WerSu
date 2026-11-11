"""Unit test for the ``20261126-directory-delete-follows-parent`` migration.

The migration only writes a SpiceDB schema, so the test drives it with
a recording client instead of a container.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from src.db.migrations.context import MigrationContext, MigrationServices


MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "db"
    / "migrations"
    / "20261126-directory-delete-follows-parent.py"
)
SCHEMA_PATH = MIGRATION_PATH.parent / "schema.zed"


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "directory_delete_migration", MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def test_migration_writes_parent_following_delete() -> None:
    written: list[str] = []

    class _RecordingSpiceDbClient:
        async def WriteSchema(self, request) -> None:
            written.append(request.schema)

    module = _load_migration()
    migration = module.Migration(MIGRATION_PATH, lambda name, instance: None)

    await migration.up(
        MigrationContext(
            db=None,
            spicedb_client=_RecordingSpiceDbClient(),
            services=MigrationServices(),
        )
    )

    assert len(written) == 1
    schema = written[0]
    # The directory delete now follows its parent (directory or shelf).
    assert "    permission delete = has_admin + parent->delete" in schema
    # The note cascade still follows the parent directory.
    assert "    permission delete = has_admin + parent_directory->delete" in schema


def test_embedded_schema_matches_canonical_schema() -> None:
    module = _load_migration()

    assert (
        module.DIRECTORY_DELETE_FOLLOWS_PARENT_SCHEMA_ZED
        == SCHEMA_PATH.read_text(encoding="utf-8")
    )
