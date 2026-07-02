# Authorization and Authentication with JWTs

The WerSu gRPC service currently only uses JWTs for public attachment access.
When a user, using a share link, accesses a note (with a, from the proxy created JWT-Token),
then the internally used `UserContextABC.is_temporary()` will be true. Hence the `NoteService`
will check the note content and scan it for attachments (by url). For each attachment found, it
will check, if the public share user has access to. If this is the case, then the service will
fill out the credentials within the `UserTokens`, which gets returned along the `NoteEntity`.
`UserTokens` then has a dictionary, which maps an attachment_id to an JWT-Token, which can
get used (`url?jwt=${JWT}`) for 15 minutes to access attachments. WerSu Rest will be the Proxy,
which evaluates the tokens generated here.

## Flow: share-link note access + per-attachment JWTs

The diagram below covers two requests back-to-back:

1. `AccessShare` returns the note and, for every embedded attachment the
   share user can read, a freshly minted 15-minute JWT.
2. The browser uses one of those JWTs (`url?jwt=...`) to fetch the
   attachment binary from the REST Proxy, which forwards the token to
   `GetAttachment` so WerSu-gRPC can authenticate the request by the
   `sub`/`att` claims alone.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Browser
    participant REST as REST-Proxy
    participant gRPC as WerSu-gRPC
    participant Provider as PyJwtProvider
    participant Repo as NoteRepo / UserRepo
    participant S3 as Garage (S3)

    Note over User,gRPC: Phase 1 - AccessShare returns note + per-attachment JWTs
    User->>Browser: open /public/n/<share_id>
    Browser->>REST: GET /api/share?share_id=<share_id>
    REST->>gRPC: rpc AccessShare(share_id)
    gRPC->>Repo: load share (note_id, access_as)
    Repo-->>gRPC: NoteShareEntity
    gRPC->>Repo: build UserContext(access_as) and fetch user
    Repo-->>gRPC: UserEntity with type="temporary"
    gRPC->>gRPC: UserContext.is_temporary_user() == true
    gRPC->>Repo: select_by_id(note_id, ctx)
    Repo-->>gRPC: NoteEntity (content + embedded attachment urls)
    loop for each attachment url in note content
        gRPC->>Repo: check access_as has read on attachment_id
        alt share user may read attachment
            gRPC->>Provider: create_attachment_token(sub=access_as, att=attachment_id, ttl=15m)
            Provider-->>gRPC: JWT (iss, sub, att, iat, exp)
            gRPC->>gRPC: UserTokens[attachment_id] = JWT
        else no access
            gRPC->>gRPC: skip (no entry in UserTokens)
        end
    end
    gRPC-->>REST: NoteEntity + UserTokens
    REST-->>Browser: note payload with JWTs

    Note over User,S3: Phase 2 - Browser downloads one attachment via JWT
    Browser->>REST: GET <attachment_url>?jwt=<JWT>
    REST->>gRPC: rpc GetAttachment(key, token=JWT)
    gRPC->>Provider: verify_attachment_token(JWT, expected_attachment_id=key)
    alt valid signature, not expired, att == key
        Provider-->>gRPC: AttachmentTokenClaims(sub=access_as, att=key, ...)
        gRPC->>gRPC: authenticate as claims.sub (no permission-repo check)
        gRPC->>Repo: fetch attachment content for key
        Repo-->>gRPC: Attachment bytes
        gRPC-->>REST: Attachment message
    else JwtError (bad signature / expired / att mismatch)
        Provider-->>gRPC: raise JwtError
        gRPC-->>REST: PERMISSION_DENIED
    end
    REST->>S3: GET s3://bucket/key (only when proxy streams bytes itself)
    S3-->>REST: object bytes
    REST-->>Browser: attachment bytes
```

## Token shape

```json
{
    "iss": "WerSu gRPC",
    "sub": "<user_id of the share's access_as user>",
    "att": "<attachment_id>",
    "iat": "<now (unix seconds)>",
    "exp": "<now + 900>"
}
```

* `iss` is fixed at construction time of `PyJwtProvider`. The same
  secret (`JWT_SECRET` env var) is used by the REST Proxy when it
  forwards `url?jwt=...` to `GetAttachment`, so both sides verify the
  same key.
* `sub` carries the share's `access_as` user id. The receiver treats
  it as the authenticated user id without an extra permission lookup.
* `att` is the binding to one specific attachment. `verify_attachment_token`
  rejects the call when `att != expected_attachment_id`, so a token
  minted for attachment A cannot be replayed against attachment B.
* Lifetime is 15 minutes by default (`ttl_seconds=15 * 60`).
