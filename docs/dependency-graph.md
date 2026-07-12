# Dependency Graph

This document describes how `src/main.py` wires the WerSu-gRPC service
together.  Every collaborator is constructed in `serve()` and handed to
the layer above it via constructor injection; nothing else in the
codebase knows how to build a concrete class.

The Construction Root (`serve()` in `src/main.py`) is the only place
in the codebase that knows about every concrete class.  Every
collaborator below it only depends on the ABCs defined in `src/api/`.

To keep it simple, this page only describes the static dependency
graph - failures, retries and graceful shutdown are handled elsewhere.

For an end-to-end view across all services, see the project-structure
doc instead.

---

## `GrpcNoteService`

Handles note CRUD plus embedding-backed search.  Built by the
Construction Root with `repo`, `log`, `to_grpc`, `context_factory`.

```mermaid
flowchart TD
    NoteSvc[GrpcNoteService]
    NoteRepo[NoteRepoFacade]
    PermRepo[PermissionRepoABC]
    DirRepo[DirectoryRepo]
    NoteVerRepo[NoteVersionRepoABC]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]

    NoteSvc -->|repo| NoteRepo
    NoteSvc -->|context_factory| Factory
    NoteRepo --> PermRepo
    NoteRepo --> DirRepo
    NoteRepo --> NoteVerRepo
    Factory --> RepoUC
```

What `GrpcNoteService` calls on each request:

1. `await self._context.create(request.user_id / author_id)` to
   build a `RepoUserContext`.
2. Passes that context into `NoteRepoFacade.select_by_id`, `.insert`,
   `.update`, `.delete`, or `.search_notes`.

---

## `GrpcDirectoryService`

Reads and writes directories plus their SpiceDB relations.  Built by
the Construction Root with `directory_repo`, `log`, `to_grpc`,
`context_factory`.

```mermaid
flowchart TD
    GrpcDirSvc[GrpcDirectoryService]
    DirRepo[DirectoryRepo]
    PermRepo[PermissionRepoABC]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]

    GrpcDirSvc -->|directory_repo| DirRepo
    GrpcDirSvc --> Factory
    DirRepo --> PermRepo
    Factory --> RepoUC
```

`GrpcDirectoryService` only constructs a `RepoUserContext` for the
`GetDirectories` listing path; the other methods operate on the
directory id alone and let `DirectoryRepo` enforce permissions
against SpiceDB.

---

## `GrpcNoteVersionService`

Streams note version history and restores previous versions.  Built by
the Construction Root with `note_repo`, `version_repo`,
`directory_activity_service`, `log`, `to_grpc`, `context_factory`.

```mermaid
flowchart TD
    GrpcNoteVerSvc[GrpcNoteVersionService]
    NoteRepo[NoteRepoFacade]
    NoteVerRepo[NoteVersionRepoABC]
    DirActSvc[DirectoryActivityService]
    PermRepo[PermissionRepoABC]
    DirRepo[DirectoryRepo]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]

    GrpcNoteVerSvc -->|note_repo| NoteRepo
    GrpcNoteVerSvc -->|version_repo| NoteVerRepo
    GrpcNoteVerSvc -->|directory_activity_service| DirActSvc
    GrpcNoteVerSvc --> Factory
    NoteRepo --> PermRepo
    NoteRepo --> DirRepo
    DirActSvc --> NoteVerRepo
    DirActSvc --> DirRepo
    Factory --> RepoUC
```

`GetDirectoryActivity` is the one method that goes through
`DirectoryActivityService.list_directory_activity`; everything else
talks to `version_repo` and `note_repo` directly.

---

## `GrpcPermissionService`

Manages note and directory permission relationships.  Built by the
Construction Root with `permission_service`, `log`, `to_grpc`,
`context_factory`.

```mermaid
flowchart TD
    GrpcPermSvc[GrpcPermissionService]
    PermSvc[PermissionServiceRepo]
    NoteRepo[NoteRepoFacade]
    DirRepo[DirectoryRepo]
    PermRepo[PermissionRepoABC]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]

    GrpcPermSvc -->|permission_service| PermSvc
    GrpcPermSvc --> Factory
    PermSvc --> PermRepo
    PermSvc --> NoteRepo
    PermSvc --> DirRepo
    NoteRepo --> PermRepo
    NoteRepo --> DirRepo
    Factory --> RepoUC
```

Every RPC builds a fresh `actor: UserContextABC` and passes it into
`PermissionServiceRepo.list_relationships`,
`create_relationship`, `delete_relationship`, or
`replace_relationships`.

---

## `GrpcAttachmentService`

Uploads, downloads, links and unlinks note attachments.  Built by the
Construction Root with `attachment_service`, `log`, `to_grpc`,
`context_factory`.

```mermaid
flowchart TD
    GrpcAttSvc[GrpcAttachmentService]
    AttFacade[AttachmentFacade]
    AttRepo[AttachmentsRepoABC]
    AttMetaRepo[AttachmentsMetadataRepoABC]
    PermRepo[PermissionRepoABC]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]

    GrpcAttSvc -->|attachment_service| AttFacade
    GrpcAttSvc --> Factory
    AttFacade --> AttRepo
    AttFacade --> AttMetaRepo
    AttFacade --> PermRepo
    Factory --> RepoUC
```

The attachment facade enforces the permission check before delegating
to the S3 repo (`AttRepo`) and the Postgres-backed metadata repo
(`AttMetaRepo`).

---

## `GrpcUserService`

Creates and looks up users.  Built by the Construction Root with
`user_service`, `log`, `to_grpc`.

```mermaid
flowchart TD
    GrpcUserSvc[GrpcUserService]
    UserSvc[UserService]
    UserRepo[UserRepoABC]
    DirRepo[DirectoryRepo]

    GrpcUserSvc -->|user_service| UserSvc
    UserSvc --> UserRepo
    UserSvc --> DirRepo
```

`GrpcUserService` is the only gRPC adapter that does not depend on
`RepoContextFactory` - `UserService` reads the user directly from
`UserRepoABC` and bootstraps the default zettelkasten directories via
`DirectoryRepo`.

---

## `GrpcSharingService`

Handles note shares (create, update, list, delete) and the public
share-link access path.  Built by the Construction Root with
`sharing_service`, `share_access_service`, `log`, `to_grpc`,
`context_factory`.

```mermaid
flowchart TD
    GrpcShareSvc[GrpcSharingService]
    SharingSvc[DefaultSharingService]
    ShareAccSvc[ShareAccessService]
    Factory[RepoContextFactory]
    RepoUC[RepoUserContext]
    ShareFacade[ShareActionFacade]
    ShareRepo[SharingRepoABC]
    PermRepo[PermissionRepoABC]
    PermSvc[PermissionServiceRepo]
    UserRepo[UserRepoABC]
    UserActionRepo[UserActionRepoABC]
    NoteRepo[NoteRepoFacade]
    DirRepo[DirectoryRepo]

    GrpcShareSvc -->|sharing_service| SharingSvc
    GrpcShareSvc -->|share_access_service| ShareAccSvc
    GrpcShareSvc --> Factory
    Factory --> RepoUC

    SharingSvc --> ShareFacade
    SharingSvc --> PermRepo
    SharingSvc --> PermSvc
    SharingSvc --> UserRepo

    ShareAccSvc --> ShareRepo
    ShareAccSvc --> PermRepo
    ShareAccSvc --> UserRepo
    ShareAccSvc --> UserActionRepo
    ShareAccSvc --> Factory

    ShareFacade --> ShareRepo
    ShareFacade --> UserRepo
    ShareFacade --> UserActionRepo

    PermSvc --> PermRepo
    PermSvc --> NoteRepo
    PermSvc --> DirRepo
```

`AccessShare` is the one method that goes through `ShareAccessService`
and uses the share's `access_as` user id via the factory.  The rest
of the CRUD surface flows through `DefaultSharingService`, which in
turn composes a `ShareActionFacade` to keep the temp-user + share-row
+ scheduled-disable-action writes in one place.

---

## How the Construction Root builds them

The order in `serve()` is roughly:

1. **External clients** — Postgres `Database`, SpiceDB async client,
   S3 client, per-table `Table` objects.
2. **Repos** — `UserPostgresRepo`, `NotePermissionRepoSpicedb`,
   `DirectoryRepoFacade`, `NoteVersionPostgresRepo`,
   `NoteRepoFacade`, `AttachmentsS3Repo`,
   `AttachmentsMetadataPostgresRepo`, `SharingPostgresRepo`,
   `UserActionPostgresRepo`.
3. **Auth helpers** — `PyJwtProvider(secret=jwt_secret)` and
   `RepoContextFactory(user_repo=user_repo)`.
4. **Services** — `AttachmentFacade`, `PermissionServiceRepo`,
   `DirectoryActivityService`, `UserService`,
   `DefaultSharingService`, `ShareAccessService`.
5. **gRPC adapters** — the seven `GrpcXService` classes whose
   dependencies are documented one per section above.