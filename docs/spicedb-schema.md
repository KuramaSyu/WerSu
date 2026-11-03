# SpiceDB Authorization Schema

This document describes the authorization model used by WerSu. It is built on
[SpiceDB](https://authzed.com/docs/guides/intro), an implementation of
Zanzibar-style authorization.

The canonical schema lives in `src/db/migrations/schema.zed`; the latest
schema migration embeds the same text.

## Objects

- **User** — a user in the system
- **Role** — a bundle of users, with delegable membership
- **Shelf** — a flat grouping of books
- **Directory** — a book: a folder/container for notes, nested under other
  books or attached to shelves
- **Note** — a document
- **Attachment** — a file linked to a note or directly to its uploader

## Core concepts

### Relations vs permissions

A SpiceDB **relationship** may only point at a **relation**, never at a
computed permission. Therefore every capability that can be held or passed on
is a relation; `can_*` names are computed permissions and are only used with
`has_permission` checks.

### Access and delegation levels

| Level | Relation (note/directory/shelf) | Meaning |
|---|---|---|
| 0 access | `reader` / `writer` / `admin` | can read / write / administer |
| 1 grant | `grant_read` / `grant_write` / `grant_admin` | can grant that access level |
| 2 delegate | `delegate_read` / `delegate_write` / `delegate_admin` | can appoint granters |

There is no level 3 (`grant_grant_*`): delegation chains terminate at level 2.

Writing a level-0 tuple requires `can_grant_<level>`, writing a level-1 tuple
requires `can_delegate_<level>`, and writing a level-2 tuple requires
`can_assign_delegate_<level>`.

### Subjects

- `user:<id>` — a plain user
- `role:<id>#member` — every member of a role (userset)

Role subjects are written as `...@role:<id>#member` and the read path preserves
the `#member` qualifier.

## Role

```zed
definition role {
    relation administrator: user | role#member
    relation member: user

    relation grant_member: user | role#member
    relation delegate_member: user | role#member

    permission can_grant_member = administrator + grant_member + delegate_member
    permission can_delegate_member = administrator + delegate_member
    permission can_assign_delegate_member = administrator
}
```

| Relation | Description |
|---|---|
| `administrator` | manages role metadata and holds every membership capability |
| `member` | role membership (written by `RoleService`, not the share API) |
| `grant_member` | may add/remove members |
| `delegate_member` | may appoint `grant_member` holders |

Membership writes are gated on `can_grant_member`; role metadata writes are
gated on `administrator`.

## Shelf / Directory

Both use the same access and delegation model:

```zed
relation owner: user | role#member
relation admin: user | role#member
relation writer: user | role#member
relation reader: user | role#member

relation grant_read: user | role#member
relation grant_write: user | role#member
relation grant_admin: user | role#member
relation delegate_read: user | role#member
relation delegate_write: user | role#member
relation delegate_admin: user | role#member

permission can_grant_read = admin + owner + grant_read + delegate_read
permission can_grant_write = admin + owner + grant_write + delegate_write
permission can_grant_admin = owner + grant_admin + delegate_admin
permission can_delegate_read = admin + owner + delegate_read
permission can_delegate_write = admin + owner + delegate_write
permission can_delegate_admin = owner + delegate_admin
permission can_assign_delegate_read = admin + owner
permission can_assign_delegate_write = admin + owner
permission can_assign_delegate_admin = owner

permission has_admin = admin + owner
permission delete = has_admin
permission write = writer + has_admin
permission view = reader + write
```

Directories additionally have `relation parent: directory | shelf`.

## Note

```zed
relation parent_directory: directory
relation owner: user | role#member
relation admin: user | role#member
relation writer: user | role#member
relation reader: user | role#member

// same grant_* / delegate_* relations and can_* permissions as above

permission has_admin = owner + admin + parent_directory->has_admin
permission delete = has_admin + parent_directory->delete
permission write = owner + writer + admin + parent_directory->write
permission view = reader + write
```

`has_admin` cascades from the parent directory, so directory admins can manage
note-level settings and rules.

## Attachment

```zed
definition attachment {
    relation parent_note: note
    relation parent_user: user

    permission delete = parent_note->delete + parent_user
    permission write = parent_note->write + parent_user
    permission view = parent_note->view + parent_user
}
```

## Policy matrix

Who may write which relation:

| Actor holds | reader | writer | admin | grant_read | grant_write | grant_admin | delegate_read | delegate_write | delegate_admin |
|---|---|---|---|---|---|---|---|---|---|
| reader | – | – | – | – | – | – | – | – | – |
| writer | – | – | – | – | – | – | – | – | – |
| admin | ✅ | ✅ | – | ✅ | ✅ | – | ✅ | ✅ | – |
| owner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| grant_read | ✅ | – | – | – | – | – | – | – | – |
| grant_write | – | ✅ | – | – | – | – | – | – | – |
| grant_admin | – | – | ✅ | – | – | – | – | – | – |
| delegate_read | ✅ | – | – | ✅ | – | – | – | – | – |
| delegate_write | – | ✅ | – | – | ✅ | – | – | – | – |
| delegate_admin | – | – | ✅ | – | – | ✅ | – | – | – |

Role / membership:

| Actor holds | member | grant_member | delegate_member |
|---|---|---|---|
| administrator | ✅ | ✅ | ✅ |
| grant_member | ✅ | – | – |
| delegate_member | ✅ | ✅ | – |

Revocation uses the same capability as granting.

## Internal relations

`owner` (note/directory/shelf) is never writable through the share API. It is
written only by the owning facade on creation or by a dedicated
ownership-transfer flow. `replace_relationships` never removes an existing
owner tuple.

The legacy `edit_permissions` and `manage` permissions have been removed.
Callers that previously checked `edit_permissions` now use `has_admin`;
public-link sharing uses `can_grant_read` / `can_grant_write`; role metadata
uses the `administrator` relation.

## Sharing examples

```
note:123#reader@user:bob
note:123#writer@role:eng#member
directory:456#admin@role:eng#member
shelf:789#reader@user:alice

note:123#grant_read@user:carol      # carol may add readers
note:123#delegate_read@user:dave    # dave may appoint grant_read holders
```

Revoke by deleting the same tuple.