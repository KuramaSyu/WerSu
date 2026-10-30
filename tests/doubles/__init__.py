"""Reusable test doubles for the whole suite.

See :mod:`tests.doubles` README for the taxonomy of doubles.
"""

from __future__ import annotations

from tests.doubles import dummies  # noqa: F401
from tests.doubles import stubs  # noqa: F401
from tests.doubles import spys  # noqa: F401
from tests.doubles import fakes  # noqa: F401
from tests.doubles import fakespys  # noqa: F401

from tests.doubles.dummies import _DummyShelfRepo  # noqa: F401
from tests.doubles.dummies import _DummyUserContext  # noqa: F401
from tests.doubles.dummies import _DummySpiceDbClient  # noqa: F401
from tests.doubles.stubs import silent_logger  # noqa: F401
from tests.doubles.stubs import _StubUserContextFactory  # noqa: F401
from tests.doubles.stubs import _StubViewPermissionRepo  # noqa: F401
from tests.doubles.stubs import _StubEmbeddingGenerator  # noqa: F401
from tests.doubles.stubs import _StubEmbeddingRepo  # noqa: F401
from tests.doubles.stubs import _StubVersionRepo  # noqa: F401
from tests.doubles.spys import _RecordingDatabase  # noqa: F401
from tests.doubles.spys import _Call  # noqa: F401
from tests.doubles.spys import RecordingTable  # noqa: F401
from tests.doubles.spys import SpyVisitor  # noqa: F401
from tests.doubles.spys import make_relationship  # noqa: F401
from tests.doubles.spys import _RecordingEmbeddingGenerator  # noqa: F401
from tests.doubles.spys import _RecordingEmbeddingRepo  # noqa: F401
from tests.doubles.fakes import InMemoryAttachmentRepo  # noqa: F401
from tests.doubles.fakes import InMemoryAttachmentMetadataRepo  # noqa: F401
from tests.doubles.fakes import ManualClock  # noqa: F401
from tests.doubles.fakes import InMemoryTaskHandle  # noqa: F401
from tests.doubles.fakes import InMemoryTaskSpawner  # noqa: F401
from tests.doubles.fakes import InMemoryPermissionRepo  # noqa: F401
from tests.doubles.fakes import InMemoryRuleRepo  # noqa: F401
from tests.doubles.fakes import InMemoryShelfRepo  # noqa: F401
from tests.doubles.fakes import _FakeDatabase  # noqa: F401
from tests.doubles.fakes import _FakeNoteContentRepo  # noqa: F401
from tests.doubles.fakes import _FakeDirectorySubdirectoryTable  # noqa: F401
from tests.doubles.fakes import _FakeDirectoryNoteTable  # noqa: F401
from tests.doubles.fakes import _FakeDirectoryTable  # noqa: F401
from tests.doubles.fakes import _FakeDirectoryTagsTable  # noqa: F401
from tests.doubles.fakes import _project  # noqa: F401
from tests.doubles.fakes import _FakeCombinedNoteRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpyActivityLoggerService  # noqa: F401
from tests.doubles.fakespys import _FakeSpyActivityRepo  # noqa: F401
from tests.doubles.fakespys import FakeSpyBackgroundProcess  # noqa: F401
from tests.doubles.fakespys import _FakeSpyDirectoryService  # noqa: F401
from tests.doubles.fakespys import _FakeSpyPermissionService  # noqa: F401
from tests.doubles.fakespys import _FakeSpyPreferencesRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpySharingRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpyShelfService  # noqa: F401
from tests.doubles.fakespys import _FakeSpyUserActionRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpyUserRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpyDirectoryRepo  # noqa: F401
from tests.doubles.fakespys import _FakeSpyNoteRepoFacade  # noqa: F401
from tests.doubles.fakespys import _FakeSpyJwtProvider  # noqa: F401
from tests.doubles.fakespys import _FakeSpyNoteService  # noqa: F401
from tests.doubles.fakespys import _FakeSpyAttachmentFacade  # noqa: F401
from tests.doubles.fakespys import _FakeSpyTagRepo  # noqa: F401
