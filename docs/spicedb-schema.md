# SpiceDB Authorization Schema

This document describes the authorization model used by WerSu. The authorization system is built on [SpiceDB](https://authzed.com/docs/guides/intro), an open-source database that implements Zanzibar, Google's access control system.

## Overview

The schema defines three primary objects:
- **User** — Represents a user in the system
- **Directory** — Represents a folder/collection of notes, as well as tags, since a note can have multiple directories where it belongs to
- **Note** — Represents a document, in context of WerSu called Note

Relationships define how users relate to directories and notes, and permissions determine what actions users can perform.

## Objects & Relations

### User

A minimal object representing a user with no direct relations or permissions.

**Definition:**
```zed
definition user {}
```

**Usage Examples:**
- `user:alice` — User with ID "alice"
- `user:[global]public-1` — A global share user for public access
In real use, instead of names like alice, their UUIDv7 will be used, which comes out of the postgres database 
---

### Directory

A container for organizing notes. Supports hierarchical structure (parent directory) and role-based access control.

**Definition:**
```zed
definition directory {
    relation parent: directory
    relation owner: user
    relation admin: user
    relation writer: user
    relation reader: user

    permission delete = admin
    permission write = writer + admin
    permission view = reader + write
    permission edit_permissions = admin + owner
}
```

**Relations:**

| Relation | Type | Description |
|----------|------|-------------|
| `parent` | directory | The parent directory of this directory (hierarchical structure) |
| `owner` | user | The owner of the directory; has full control |
| `admin` | user | Administrator with delete, write, view, and edit_permissions permissions |
| `writer` | user | Can write and view the directory and its contents |
| `reader` | user | Can only view the directory and its contents |

**Permissions:**

| Permission | Granted To | Description |
|-----------|-----------|-------------|
| `view` | reader, write | Can view the directory and its contents |
| `write` | writer, admin | Can create/modify items in the directory |
| `delete` | admin | Can delete the directory and its contents |
| `edit_permissions` | admin, owner | Can modify access control for the directory |

**Permission Hierarchy:**
- `owner` and `admin` → full access
- `writer` → `write` + `view`
- `reader` → `view` only

**Examples:**
- `directory:my-fleeting-notes#owner@user:alice` — Alice owns this directory
- `directory:my-fleeting-notes#admin@user:bob` — Bob is an admin of this directory
- `directory:my-fleeting-notes#parent@directory:root` — "my-fleeting-notes" is a subdirectory of "root"
- Check: Can alice write to directory:my-fleeting-notes? → **YES** (owner has write)

---

### Note

A document/note with content that can be shared and managed with granular permissions.

**Definition:**
```zed
definition note {
    relation owner: user
    relation admin: user
    relation writer: user
    relation reader: user
    relation parent_directory: directory

    permission delete = owner + admin + parent_directory->delete
    permission write = owner + writer + admin + parent_directory->write
    permission view = reader + write
    permission edit_permissions = owner + admin + parent_directory->edit_permissions
}
```

**Relations:**

| Relation | Type | Description |
|----------|------|-------------|
| `owner` | user | The owner of the note; typically the creator |
| `admin` | user | Administrator with full permissions on the note |
| `writer` | user | Can edit and view the note |
| `reader` | user | Can only view the note (read-only access) |
| `parent_directory` | directory | The directory that contains this note |

**Permissions:**

| Permission | Granted To | Description |
|-----------|-----------|-------------|
| `view` | reader, write | Can view the note content |
| `write` | owner, writer, admin, or parent_directory with write permission | Can edit the note |
| `delete` | owner, admin, or parent_directory with delete permission | Can delete the note |
| `edit_permissions` | owner, admin, or parent_directory with edit_permissions permission | Can modify note access control |

**Permission Hierarchy:**
- `owner` → full access (view, write, delete, edit_permissions)
- `admin` → full access (view, write, delete, edit_permissions)
- `writer` → `write` + `view`
- `reader` → `view` only
- **Transitive**: Permissions inherit from parent_directory (e.g., if parent directory owner deletes, note is deleted)

**Examples:**
- `note:my-note-id#owner@user:alice` — Alice owns this note
- `note:my-note-id#parent_directory@directory:my-fleeting-notes` — Note belongs to this directory
- `note:my-note-id#reader@user:[global]public-1` — Public share: global user can read
- Check: Can charlie write to note:my-note-id if charlie is reader? → **NO**
- Check: Can charlie write to note:my-note-id if charlie has write on parent_directory? → **YES**

---

## Common Patterns

### Sharing Notes

To share a note with another user, create a relationship with the desired permission level:

```
note:123#reader@user:bob          # Bob can view note 123
note:123#writer@user:charlie      # Charlie can edit note 123
```

### Public/Global Sharing

Use a special global user prefix `[global]` to represent public shares:

```
note:456#reader@user:[global]public-share-1  # Anyone with this share link can view
note:789#reader@user:[global]user-123        # All users authenticated as [global]user-123
```

### Hierarchical Directory Permissions

Directory permissions cascade to notes:

```
directory:root#admin@user:alice
directory:root#parent@directory:subdirectory
note:my-note#parent_directory@directory:subdirectory
```

If alice is admin of `root`, she automatically has admin permissions on `subdirectory` and all notes within it (through transitive permission).

### Revoking Access

Delete a relationship to revoke a permission:

```
Delete: note:123#reader@user:bob   # Bob can no longer view note 123
```

---

## Permission Resolution

When checking if a user has permission on a resource, SpiceDB evaluates:

1. **Direct relations** — Does the user have a direct relationship?
2. **Transitive relations** — Does the user have access through a parent resource?
3. **Implied permissions** — What effective permissions does the relation grant?

**Example:**
```
Scenario: Check if alice can write to note:999

Relations:
- note:999#parent_directory@directory:my-dir
- directory:my-dir#writer@user:alice

Resolution:
- alice has no direct writer/admin/owner on note:999
- alice is writer on parent_directory
- note.write includes "parent_directory->write"
- parent_directory has write permission for writer
- Result: YES, alice can write to note:999
```

---

## Implementation Notes

### In SpiceDB API

Relations are stored as tuples in the format:
```
<object_type>:<object_id>#<relation>@<subject_type>:<subject_id>
```

Examples:
- `note:abc123#owner@user:alice`
- `directory:fleeting#admin@user:bob`
- `note:xyz#parent_directory@directory:fleeting`

### In Code

Relations are created using the `Relationship` class:

```python
from src.db.repos.note.permission import (
    Relationship, ObjectRef, SubjectRef
)

# Create a relationship
rel = Relationship(
    resource=ObjectRef(object_type="note", object_id="abc123"),
    relation="reader",
    subject=SubjectRef(object_type="user", object_id="alice")
)

# Insert into permission repo
await permission_repo.insert([rel])

# Check permission
can_view = await permission_repo.has_permission(
    user=UserContext("alice"),
    permission="view",
    resource=ObjectRef(object_type="note", object_id="abc123")
)
```

### In Tests

Use `NotePermissionRepoInMemory` for unit/integration tests:

```python
repo = NotePermissionRepoInMemory()
await repo.insert([relationship])
assert await repo.has_permission(user, "view", resource)
```

---

## Extending the Schema

To add new relations or permissions:

1. Edit `src/db/migrations/schema.zed`
2. Create a new migration file in `src/db/migrations/` (e.g., `20260601-add-feature.py`)
3. The migration should read `schema.zed` and call `spicedb_client.WriteSchema()`
4. Run migrations to apply changes

Example migration:
```python
from pathlib import Path
from authzed.api.v1 import WriteSchemaRequest
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext

class Migration(MigrationABC):
    async def up(self, ctx: MigrationContext) -> None:
        if ctx.spicedb_client is None:
            raise ValueError("SpiceDB client required")
        schema_text = Path(__file__).with_name("schema.zed").read_text()
        await ctx.spicedb_client.WriteSchema(WriteSchemaRequest(schema=schema_text))
```

---

## References

- [SpiceDB Documentation](https://authzed.com/docs)
- [Zanzibar: Google's Consistent, Global Authorization System](https://www.usenix.org/conference/usenixsecurity19/presentation/potti)
- [Zed Language Reference](https://authzed.com/docs/reference/lang)
