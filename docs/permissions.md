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
| administrator | ✅ | ✅ | ✅ |
| grant_member | ✅ | – | – |
| delegate_member | ✅ | ✅ | – |

`owner` is not writable through the share API. The legacy `edit_permissions` and `manage` permissions are gone.

> **Code:** `src/domain/share_policy.py`. `SHARE_POLICIES` holds this matrix;
> `GrantPermissionFactory` maps a relation or access level to its `can_*`
> check. The SpiceDB schema is the source of truth.
