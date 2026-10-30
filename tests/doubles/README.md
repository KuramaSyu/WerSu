# Test doubles

Everything in this directory is a **test double**: an object that stands in
for a real collaborator (repo, service, facade, database, clock, …) so a unit
test can run without booting Postgres, SpiceDB, Garage or an ML model.

The doubles are grouped by *what kind* of double they are. The taxonomy
follows Gerard Meszaros' *xUnit Test Patterns* (with the addition of
`fakespy`, i.e. a fake that also spies). Class names mirror their category:
`_Dummy*`, `_Stub*`, `_Recording*`/`Spy*`, `Fake*`/`InMemory*`, `_Mock*`,
`_FakeSpy*`.

## Taxonomy

| Type | What it is | Behaviour | Folder | Name prefix |
| --- | --- | --- | --- | --- |
| **Dummy** | An object that is passed around but never actually used. It only exists to satisfy a parameter list / ABC. Often returns `None`, `[]` or `UNDEFINED`. | inert | `dummies/` | `_Dummy*` |
| **Stub** | Provides *canned* answers to the calls a test expects. It does not record, and has no real internal state. | canned answers | `stubs/` | `_Stub*` |
| **Spy** | Records how it was called (`calls`, `insert_calls`, …) and delegates to a real or canned result. Assertions are made on the recorded calls. | records calls | `spys/` | `Recording*` / `_Recording*` / `Spy*` |
| **Fake** | A working, lightweight implementation of the real thing (usually in-memory). It behaves correctly for the paths a test exercises, but is not production-safe. | working, in-memory | `fakes/` | `Fake*` / `InMemory*` |
| **Mock** | Pre-programmed with expectations / side effects. It fails or raises in a way the test dictates (e.g. an expected `ValueError`, a unique-constraint violation). | expectation-driven | `mocks/` | `_Mock*` |
| **FakeSpy** | A **fake** (real in-memory behaviour) that **also spies** (records every call). The workhorse of this suite. | in-memory + records | `fakespys/` | `_FakeSpy*` / `FakeSpy*` |

> Rule of thumb: if a test only needs a canned value, use a **stub**; if it
> needs to assert on the interaction, use a **spy**; if it needs the
> collaborator to actually work, use a **fake** / **fakespy**.

## Implementations

### `dummies/`

| Class | File | Stands in for |
| --- | --- | --- |
| `_DummyUserContext` | `dummies/user_context.py` | `UserContextABC` – minimal actor with a user id. |
| `_DummyUserCtx` | `dummies/user_ctx.py` | `UserContextABC` – tiny actor used by shelf/rule tests. |
| `_DummyTemporaryUserContext` | `dummies/temporary_user_context.py` | temporary/shared actor returned by user-service tests. |
| `_DummyContext` | `dummies/fake_context.py` | gRPC `ServicerContext` – captures status code / details. |
| `_DummyShelfRepo` | `dummies/noop_shelf_repo.py` | `ShelfRepoABC` – every shelf operation is a no-op. |
| `_DummyTable` | `dummies/note_version_unit.py` | `TableABC` – placeholder table. |
| `_DummySpiceDbClient` | `dummies/spicedb_client.py` | SpiceDB client – no-op used during migration setup. |

### `stubs/`

| Class | File | Stands in for |
| --- | --- | --- |
| `silent_logger` | `stubs/logging.py` | logging provider returning the stdlib logger. |
| `_StubUserContextFactory` | `stubs/user_context_factory.py` | `ContextFactory` building `_DummyUserContext`s. |
| `_StubEmbeddingGenerator` / `_StubEmbeddingRepo` | `stubs/embedding.py` | embedding generator / repo with fixed vectors. |
| `_StubVersionRepo` | `stubs/version_repo.py` | `NoteVersionRepoABC` returning predefined versions. |
| `_StubViewPermissionRepo` | `stubs/view_permission_repo.py` | `PermissionRepoABC` granting `view` on seeded ids. |
| `_StubGrpcNoteVersionRepo`, `_StubGrpcNoteRepo` | `stubs/grpc_note_version_service.py` | note-version gRPC dependencies. |
| `_StubPermissionNoteRepo`, `_StubPermissionDirectoryRepo` | `stubs/permission_service.py` | permission-service dependencies. |
| `_StubMigrationsService` | `stubs/migrations_service.py` | third-party migrations service. |
| `_StubBookstackNoteService`, `_StubBookstackAttachmentFacade` | `stubs/bookstack.py` | BookStack migration ports. |
| `_StubBookstackEmergencyNoteService`, `_StubBookstackEmergencyAttachmentFacade` | `stubs/bookstack_emergency.py` | emergency BookStack migration ports. |
| `_StubBookstackExtraNoteService`, `_StubBookstackExtraAttachmentFacade` | `stubs/bookstack_extra.py` | extra BookStack migration ports. |
| `_StubMathNoteService`, `_StubMathAttachmentFacade` | `stubs/math_migrations.py` | math migration ports. |

### `spys/`

| Class | File | Stands in for |
| --- | --- | --- |
| `_RecordingDatabase` | `spys/recording_database.py` | `DatabaseABC` – records SQL and replays queued rows. |
| `RecordingTable` (+ `_Call`) | `spys/recording_table.py` | `TableABC` – records every table call, stores rows. |
| `_RecordingEmbeddingGenerator` / `_RecordingEmbeddingRepo` | `spys/embedding.py` | embedding collaborators that record invocations. |
| `SpyVisitor` | `spys/visitor.py` | `EntityVisitor` recording visited entities. |
| `_RecordingDirectoryRepo`, `_RecordingTagRepo` (+ call records) | `spys/directory_facade.py` | directory/tag repos recording CRUD calls. |
| `RecordingUserAuthRepo` (+ `_Call`) | `spys/user_auth_repo_grpc.py`, `spys/user_auth_repo_impl.py` | `UserAuthRepoABC` recording calls. |
| `_CapturingTable` | `spys/user_action_postgres_repo.py` | `TableABC` capturing SQL. |
| `_RecordingSchedulerHandle`, `_Listeners` | `spys/notifying_user_action_repo.py` | scheduler handle / listener registry. |

### `fakes/`

| Class | File | Stands in for |
| --- | --- | --- |
| `InMemoryAttachmentRepo`, `InMemoryAttachmentMetadataRepo` | `fakes/attachments.py` | attachment storage / metadata. |
| `InMemoryPermissionRepo` | `fakes/in_memory_permission_repo.py` | SpiceDB-backed permission repo. |
| `InMemoryRuleRepo` | `fakes/in_memory_rule_repo.py` | rule repo. |
| `InMemoryShelfRepo` | `fakes/in_memory_shelf_repo.py` | shelf repo. |
| `ManualClock`, `InMemoryTaskHandle`, `InMemoryTaskSpawner` | `fakes/background_process.py` | async clock / task spawner. |
| `_FakeDatabase` | `fakes/database.py` | `DatabaseABC` with queued responses. |
| `_FakeNoteContentRepo` | `fakes/note_content_repo.py` | note-content repo. |
| `_FakeCombinedNoteRepo` | `fakes/combined_note_repo.py` | combined note repo. |
| `_FakeDirectory*Table` | `fakes/directory_tables.py` | `note.directory*` tables. |
| `FakeDirectoryRepo`, `FakeTagRepo`, `FakeNoteContentRepo` | `fakes/rule_dispatcher.py` | rule-dispatcher inputs. |
| `FakeNoteContentRepo`, `FakeDirectoryRepo`, `FakeShelfRepo` | `fakes/event_context.py` | event-context repositories. |
| `FakeClock` | `fakes/async_ttl.py` | async TTL clock. |
| `_FakeLinkTable`, `_FakeUserLinkTable` | `fakes/link_tables.py` | attachment link tables. |
| `_FakeTable` | `fakes/note_version_unit.py`, `fakes/sharing_repo.py` | `TableABC` fakes. |
| `FakeDirectoryFacade` | `fakes/rule_service.py`, `fakes/shelf_service.py`, `fakes/shelf_migration.py`, `fakes/zettelkasten_strategy.py` | `DirectoryFacadeABC` per test area. |
| `_InMemoryUserRepo`, `_InMemoryDirectoryRepo`, `_InMemoryContextFactory` | `fakes/user_service.py` | user-service dependencies. |

### `mocks/`

| Class | File | Stands in for |
| --- | --- | --- |
| `_expect_value_error` | `mocks/directory_facade.py` | context manager asserting a `ValueError` is raised. |
| `_MockRaisingAuthService` | `mocks/raising_auth_service.py` | auth service that raises on demand. |
| `_MockUniqueViolationTagRepo` | `mocks/unique_violation_tag_repo.py` | tag repo mocking a unique-constraint violation. |

### `fakespys/`

| Class | File | Stands in for |
| --- | --- | --- |
| `_FakeSpyActivityLoggerService` | `fakespys/activity_logger_service.py` | `ActivityLoggerServiceABC`. |
| `_FakeSpyActivityRepo` | `fakespys/activity_repo.py` | `ActivityRepoABC`. |
| `FakeSpyBackgroundProcess` | `fakespys/background_process.py` | `BackgroundProcessABC`. |
| `_FakeSpyPermissionService` | `fakespys/permission_service.py` | `PermissionServiceABC`. |
| `_FakeSpyPreferencesRepo` | `fakespys/preferences_repo.py` | preferences repo. |
| `_FakeSpySharingRepo` | `fakespys/sharing_repo.py` | sharing repo. |
| `_FakeSpyUserActionRepo` | `fakespys/user_action_repo.py` | user-action repo. |
| `_FakeSpyUserRepo` | `fakespys/user_repo.py` | user repo. |
| `_FakeSpyShelfService` | `fakespys/shelf_service.py` | `ShelfServiceABC`. |
| `_FakeSpyDirectoryService` | `fakespys/directory_service.py` | `DirectoryServiceABC`. |
| `_FakeSpyDirectoryRepo` | `fakespys/directory_repo.py` | `DirectoryFacadeABC`. |
| `_FakeSpyActivityDirectoryRepo` | `fakespys/directory_activity.py` | `DirectoryFacadeABC` for activity tests. |
| `_FakeSpyNoteRepoFacade` | `fakespys/note_repo_facade.py` | `NoteRepoFacadeABC`. |
| `_FakeSpyTagRepo` | `fakespys/tag_repo.py` | `TagRepoABC`. |
| `_FakeSpyJwtProvider` | `fakespys/jwt_provider.py` | JWT provider. |
| `_FakeSpyGrpcNoteService`, `_FakeSpyCapturingNoteService`, `_FakeSpyUpdateNoteService` | `fakespys/grpc_note_service.py` | note-service gRPC dependencies. |
| `_FakeSpyDirectoryActivityService` | `fakespys/grpc_note_version_service.py` | directory activity service. |
| `_FakeSpyAttachmentService` | `fakespys/grpc_attachment_service.py` | attachment service. |
| `_FakeSpyAttachmentFacade`, `_FakeSpyNoteService` | `fakespys/attachment_facade.py`, `fakespys/note_service.py` | migration-service ports. |
| `_FakeSpyRoleRepo` | `fakespys/role_repo.py` | role repo. |

## Conventions

* **Every test double lives in this directory.** Test modules must not define
  their own `Fake*` / `Stub*` / `Recording*` class; import it from here
  instead. This keeps behaviour consistent and makes it obvious which kind of
  double a test relies on. `tests/test_doubles_conventions.py` enforces this.
* One module per *area*, named after the collaborator or the test area it is
  used from.
* Doubles that only one test area needs live in that area's module; genuinely
  shared doubles (e.g. `_DummyUserContext`, `_DummyContext`) live in the
  shared `dummies/` / `fakespys/` modules and are imported by everyone.
* Prefer extending an existing double over writing a new one. If a new
  behaviour is needed only for one test, configure the existing double (flags,
  queued responses, seeded state) rather than forking it.
