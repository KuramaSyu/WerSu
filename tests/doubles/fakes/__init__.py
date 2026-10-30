"""Test doubles of category: fakes."""

from __future__ import annotations

from tests.doubles.fakes.attachments import (
    InMemoryAttachmentRepo,
    InMemoryAttachmentMetadataRepo,
)

from tests.doubles.fakes.background_process import (
    ManualClock,
    InMemoryTaskHandle,
    InMemoryTaskSpawner,
)

from tests.doubles.fakes.in_memory_permission_repo import (
    InMemoryPermissionRepo,
)

from tests.doubles.fakes.in_memory_rule_repo import (
    InMemoryRuleRepo,
)

from tests.doubles.fakes.in_memory_shelf_repo import (
    InMemoryShelfRepo,
)

from tests.doubles.fakes.database import (
    _FakeDatabase,
)

from tests.doubles.fakes.note_content_repo import (
    _FakeNoteContentRepo,
)

from tests.doubles.fakes.directory_tables import (
    _FakeDirectorySubdirectoryTable,
    _FakeDirectoryNoteTable,
    _FakeDirectoryTable,
    _FakeDirectoryTagsTable,
    _project,
)

from tests.doubles.fakes.combined_note_repo import (
    _FakeCombinedNoteRepo,
)
