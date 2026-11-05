# Permissions

Access: `reader` < `writer` < `admin` < `owner`.
Capabilities: `grant_*` can grant access; `delegate_*` can appoint granters.

## Who can write what

| Actor holds | reader | writer | admin | grant_read | grant_write | grant_admin | delegate_read | delegate_write | delegate_admin |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
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

Revoking uses the same permission as granting.

## Roles

| Actor holds | member | grant_member | delegate_member |
|---|:-:|:-:|:-:|
| admin | ✅ | ✅ | ✅ |
| grant_member | ✅ | – | – |
| delegate_member | ✅ | ✅ | – |

Creating a role grants the creating user every permission of that role:
`create_role` writes `role:<id>#admin@user:<creator>` (all `role`
permissions derive from `admin`) and `role:<id>#member@user:<creator>`
(so grants made to the role also apply to the creator).

`owner` is not writable through the share API. The legacy `edit_permissions` and `manage` permissions are gone.

## Page administration

Site-wide permissions are held on the singleton `page:global` object.
The seeded `Page Administrator` role carries the `admin`,
`grant_page_admin` and `delegate_page_admin` relations; the first real
(non-system) user created is assigned to that role. Role creation is
gated on `page:global#admin`, replacing the old
`ROLE_SUPER_ADMINS` environment variable. Both `role` and `page` expose
`has_admin` so generic resource-admin checks work for them too.

> **Code:** `src/domain/share_policy.py`. `SHARE_POLICIES` holds this matrix;
> `GrantPermissionFactory` maps a relation or access level to its `can_*`
> check. The SpiceDB schema is the source of truth.
