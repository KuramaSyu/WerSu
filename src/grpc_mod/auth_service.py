"""gRPC adapter for the ``AuthService`` defined in ``auth.proto``.

Thin marshalling layer over
:class:`src.api.services.user_auth_service.UserAuthServiceABC`.
All policy lives in the service; this module only translates
proto payloads and surfaces :exc:`PermissionError` as
``grpc.StatusCode.PERMISSION_DENIED``.

Entity-to-proto conversion goes through
:class:`src.grpc_mod.converter.grpc_visitor.ConvertToGrpcVisitor`
via the visitor pattern -- the adapter just calls
``entity.convert(self._to_grpc)``.
"""

from __future__ import annotations

import traceback
from typing import Any

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.services.user_auth_service import UserAuthServiceABC
from src.api.services.user_service import UserFilter
from src.db.entities.user.passkey import PasskeyEntity
from src.db.entities.user.third_party import (
    DiscordLink,
    GoogleLink,
    ThirdPartyFilter,
)
from src.db.entities.user.user_auth import UserAuthEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.auth_pb2 import (
    Credential,
    CredentialKind,
    CreateUserAuthRequest,
    CreateUserAuthResponse,
    FindCredentialByProviderRequest,
    FindCredentialByProviderResponse,
    FindPasskeyRequest,
    FindPasskeyResponse,
    GetUserAuthRequest,
    GetUserAuthResponse,
    LinkCredentialRequest,
    LinkCredentialResponse,
    ListLinkedCredentialsRequest,
    ListLinkedCredentialsResponse,
    ListPasskeysRequest,
    ListPasskeysResponse,
    RegisterPasskeyRequest,
    RegisterPasskeyResponse,
    RevokePasskeyRequest,
    UnlinkCredentialRequest,
    UpdatePasskeyCounterRequest,
    UpdatePasskeyCounterResponse,
    UpdateUserAuthRequest,
    UpdateUserAuthResponse,
)
from src.grpc_mod.proto.auth_pb2_grpc import AuthServiceServicer


class GrpcAuthService(AuthServiceServicer):
    """gRPC adapter for the ``AuthService`` defined in ``auth.proto``.

    Args:
        user_auth_service: the auth-side service that owns all
            policy decisions.
        log: logger factory compatible with :class:`LoggingProvider`.
    """

    def __init__(
        self,
        user_auth_service: UserAuthServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
    ) -> None:
        self._service = user_auth_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc

    @staticmethod
    def _set_unset(
        context: ServicerContext[Any, Any],
        code: grpc.StatusCode,
        msg: str,
    ) -> None:
        """Set the gRPC status on `context` and stop the call."""
        context.set_code(code)
        context.set_details(msg)

    @staticmethod
    def _build_filter(request: GetUserAuthRequest) -> UserFilter:
        """Translate `request`'s ``identifier`` oneof into a :class:`UserFilter`."""
        which = request.WhichOneof("identifier")
        if which == "user_id":
            return UserFilter(user_id=request.user_id)
        if which == "email":
            return UserFilter(email=request.email)
        if which == "discord_id":
            return UserFilter(discord_id=request.discord_id)
        return UserFilter()

    @log_service_call()
    async def GetUserAuth(
        self,
        request: GetUserAuthRequest,
        context: ServicerContext[GetUserAuthRequest, GetUserAuthResponse],
    ) -> GetUserAuthResponse:
        """Resolve a user by one of the login identifiers."""
        try:
            entity = await self._service.get_user(self._build_filter(request))
            if entity is None:
                self.log.debug(f"GetUserAuth: no match for {request}")
                self._set_unset(
                    context, grpc.StatusCode.NOT_FOUND, "User not found"
                )
                return GetUserAuthResponse()
            return GetUserAuthResponse(user=entity.convert(self._to_grpc))
        except Exception:
            self.log.error(f"Error in GetUserAuth: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while looking up user",
            )
            return GetUserAuthResponse()

    @log_service_call()
    async def CreateUserAuth(
        self,
        request: CreateUserAuthRequest,
        context: ServicerContext[CreateUserAuthRequest, CreateUserAuthResponse],
    ) -> CreateUserAuthResponse:
        """Create a new user from email + (optional) avatar URL.

        ``password_hash`` is consumed by the proto but not
        persisted here -- the caller (REST's signup handler) is
        responsible for hashing and storing via
        :attr:`UserAuthServiceABC.passwords`.
        """
        try:
            created = await self._service.create_user(
                UserAuthEntity(
                    email=request.email or None,
                    username=request.username or None,
                    avatar=request.avatar_url,
                )
            )
            return CreateUserAuthResponse(user=created.convert(self._to_grpc))
        except Exception:
            self.log.error(f"Error in CreateUserAuth: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while creating user",
            )
            return CreateUserAuthResponse()

    @log_service_call()
    async def UpdateUserAuth(
        self,
        request: UpdateUserAuthRequest,
        context: ServicerContext[UpdateUserAuthRequest, UpdateUserAuthResponse],
    ) -> UpdateUserAuthResponse:
        """Apply one or more tri-state changes to a user row.

        The ``requester_id == user_id`` check lives in the service
        layer; :exc:`PermissionError` surfaces as
        ``grpc.StatusCode.PERMISSION_DENIED``.
        """
        try:
            if not request.user_id:
                self._set_unset(
                    context,
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "user_id is required",
                )
                return UpdateUserAuthResponse()

            update_fields: dict[str, object] = {"id": request.user_id}

            which = request.WhichOneof("username_change")
            if which == "username_set":
                update_fields["username"] = request.username_set or None
            elif which == "username_clear":
                update_fields["username"] = None

            which = request.WhichOneof("email_change")
            if which == "email_set":
                update_fields["email"] = request.email_set or None
            elif which == "email_clear":
                update_fields["email"] = None

            which = request.WhichOneof("avatar_url_change")
            if which == "avatar_url_set":
                update_fields["avatar"] = request.avatar_url_set or ""
            elif which == "avatar_url_clear":
                # REST contract: "unset" clears to empty string
                # (not NULL); the REST controller renders "" as
                # JSON null.
                update_fields["avatar"] = ""

            updated = await self._service.update_user(
                UserAuthEntity(**update_fields),  # type: ignore[arg-type]
                requester_id=request.requester_id,
            )
            return UpdateUserAuthResponse(user=updated.convert(self._to_grpc))
        except PermissionError:
            self.log.warning(
                f"UpdateUserAuth permission denied: requester_id={request.requester_id} "
                f"user_id={request.user_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return UpdateUserAuthResponse()
        except Exception:
            self.log.error(f"Error in UpdateUserAuth: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while updating user",
            )
            return UpdateUserAuthResponse()

    @log_service_call()
    async def RegisterPasskey(
        self,
        request: RegisterPasskeyRequest,
        context: ServicerContext[RegisterPasskeyRequest, RegisterPasskeyResponse],
    ) -> RegisterPasskeyResponse:
        """Register a new WebAuthn passkey for the caller."""
        try:
            passkey = PasskeyEntity(
                credential_id=request.credential_id,
                public_key=request.public_key,
                sign_count=0,
                transports=list(request.transports),
                aaguid=bytes(request.aaguid) if request.aaguid else None,
                backup_eligible=request.backup_eligible,
                backup_state=request.backup_state,
                user_verified=request.user_verified,
                friendly_name=request.friendly_name or None,
            )
            stored = await self._service.passkeys.register_passkey(
                request.user_id,
                request.requester_id,
                passkey,
            )
            return RegisterPasskeyResponse(passkey=stored.convert(self._to_grpc))
        except PermissionError:
            self.log.warning(
                f"RegisterPasskey permission denied: requester_id={request.requester_id} "
                f"user_id={request.user_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return RegisterPasskeyResponse()
        except Exception:
            self.log.error(f"Error in RegisterPasskey: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while registering passkey",
            )
            return RegisterPasskeyResponse()

    @log_service_call()
    async def FindPasskey(
        self,
        request: FindPasskeyRequest,
        context: ServicerContext[FindPasskeyRequest, FindPasskeyResponse],
    ) -> FindPasskeyResponse:
        """Look up a passkey by its WebAuthn credential id."""
        try:
            found = await self._service.passkeys.find_passkey(
                bytes(request.credential_id)
            )
            if found is None:
                self.log.debug(
                    f"FindPasskey: no match for credential_id={bytes(request.credential_id)!r}"
                )
                self._set_unset(
                    context, grpc.StatusCode.NOT_FOUND, "passkey not found"
                )
                return FindPasskeyResponse()
            return FindPasskeyResponse(passkey=found.convert(self._to_grpc))
        except Exception:
            self.log.error(f"Error in FindPasskey: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while looking up passkey",
            )
            return FindPasskeyResponse()

    @log_service_call()
    async def ListPasskeys(
        self,
        request: ListPasskeysRequest,
        context: ServicerContext[ListPasskeysRequest, ListPasskeysResponse],
    ) -> ListPasskeysResponse:
        """List a user's passkeys (revoked hidden by default)."""
        try:
            keys = await self._service.passkeys.list_passkeys(
                request.user_id, include_revoked=request.include_revoked
            )
            return ListPasskeysResponse(
                passkeys=[k.convert(self._to_grpc) for k in keys]
            )
        except Exception:
            self.log.error(f"Error in ListPasskeys: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while listing passkeys",
            )
            return ListPasskeysResponse()

    @log_service_call()
    async def UpdatePasskeyCounter(
        self,
        request: UpdatePasskeyCounterRequest,
        context: ServicerContext[
            UpdatePasskeyCounterRequest, UpdatePasskeyCounterResponse
        ],
    ) -> UpdatePasskeyCounterResponse:
        """Bump the sign counter after a successful assertion."""
        try:
            updated = await self._service.passkeys.update_sign_count(
                request.passkey_id,
                request.new_sign_count,
                request.requester_id,
            )
            return UpdatePasskeyCounterResponse(
                passkey=updated.convert(self._to_grpc)
            )
        except PermissionError:
            self.log.warning(
                f"UpdatePasskeyCounter permission denied: requester_id={request.requester_id} "
                f"passkey_id={request.passkey_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return UpdatePasskeyCounterResponse()
        except ValueError:
            self.log.warning(
                f"UpdatePasskeyCounter rejected: passkey_id={request.passkey_id} "
                f"new_sign_count={request.new_sign_count}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.FAILED_PRECONDITION,
                "new_sign_count must be strictly greater than current",
            )
            return UpdatePasskeyCounterResponse()
        except Exception:
            self.log.error(
                f"Error in UpdatePasskeyCounter: {traceback.format_exc()}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while updating sign counter",
            )
            return UpdatePasskeyCounterResponse()

    @log_service_call()
    async def RevokePasskey(
        self,
        request: RevokePasskeyRequest,
        context: ServicerContext[RevokePasskeyRequest, Empty],
    ) -> Empty:
        """Revoke a passkey."""
        try:
            await self._service.passkeys.revoke_passkey(
                request.passkey_id, request.requester_id
            )
            return Empty()
        except PermissionError:
            self.log.warning(
                f"RevokePasskey permission denied: requester_id={request.requester_id} "
                f"passkey_id={request.passkey_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return Empty()
        except Exception:
            self.log.error(f"Error in RevokePasskey: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while revoking passkey",
            )
            return Empty()

    @log_service_call()
    async def FindCredentialByProvider(
        self,
        request: FindCredentialByProviderRequest,
        context: ServicerContext[
            FindCredentialByProviderRequest, FindCredentialByProviderResponse
        ],
    ) -> FindCredentialByProviderResponse:
        """Resolve a credential + the owning user.

        `request.kind` selects the credential table; the
        ``identifier`` oneof carries the lookup value (Discord id,
        Google id, or email -- email currently keys off the user's
        email column, not the password row).
        """
        try:
            kind = request.kind
            which = request.WhichOneof("identifier")
            if kind == CredentialKind.CREDENTIAL_KIND_DISCORD and which == "discord_id":
                links = await self._service.third_parties.find_third_party(
                    ThirdPartyFilter(
                        provider="discord",
                        provider_user_id=str(request.discord_id),
                    )
                )
                if not links:
                    self.log.debug(
                        f"FindCredentialByProvider: no discord link for "
                        f"discord_id={request.discord_id}"
                    )
                    self._set_unset(
                        context, grpc.StatusCode.NOT_FOUND, "credential not found"
                    )
                    return FindCredentialByProviderResponse()
                link = links[0]
                user = await self._service.get_user(UserFilter(user_id=link.user_id))
                if user is None:
                    self._set_unset(
                        context,
                        grpc.StatusCode.NOT_FOUND,
                        "user for credential not found",
                    )
                    return FindCredentialByProviderResponse()
                return FindCredentialByProviderResponse(
                    credential=link.convert(self._to_grpc),
                    user=user.convert(self._to_grpc),
                )

            if kind == CredentialKind.CREDENTIAL_KIND_GOOGLE and which == "google_id":
                links = await self._service.third_parties.find_third_party(
                    ThirdPartyFilter(
                        provider="google",
                        provider_user_id=request.google_id,
                    )
                )
                if not links:
                    self.log.debug(
                        f"FindCredentialByProvider: no google link for "
                        f"google_id={request.google_id}"
                    )
                    self._set_unset(
                        context, grpc.StatusCode.NOT_FOUND, "credential not found"
                    )
                    return FindCredentialByProviderResponse()
                link = links[0]
                user = await self._service.get_user(UserFilter(user_id=link.user_id))
                if user is None:
                    self._set_unset(
                        context,
                        grpc.StatusCode.NOT_FOUND,
                        "user for credential not found",
                    )
                    return FindCredentialByProviderResponse()
                return FindCredentialByProviderResponse(
                    credential=link.convert(self._to_grpc),
                    user=user.convert(self._to_grpc),
                )

            if kind == CredentialKind.CREDENTIAL_KIND_PASSWORD and which == "email":
                user = await self._service.get_user(UserFilter(email=request.email))
                if user is None or user.id is UNDEFINED:
                    self._set_unset(
                        context, grpc.StatusCode.NOT_FOUND, "credential not found"
                    )
                    return FindCredentialByProviderResponse()
                password = await self._service.passwords.find_password(user.id)
                if password is None:
                    self._set_unset(
                        context, grpc.StatusCode.NOT_FOUND, "credential not found"
                    )
                    return FindCredentialByProviderResponse()
                return FindCredentialByProviderResponse(
                    credential=password.convert(self._to_grpc),
                    user=user.convert(self._to_grpc),
                )

            if kind == CredentialKind.CREDENTIAL_KIND_PASSKEY:
                # ``identifier`` for passkey is the ``id`` (server-assigned),
                # not the WebAuthn credential id -- REST has already
                # resolved the row by the WebAuthn id before calling us.
                passkey = await self._service.passkeys.list_passkeys(
                    user_id="" if which != "email" else request.email,
                    include_revoked=False,
                )
                # Caller will normally set ``email`` to a placeholder
                # unused by REST; the passkey id lives on the request
                # as a fake identifier.  If the request didn't carry a
                # known identifier, fall through to NOT_FOUND.
                self._set_unset(
                    context, grpc.StatusCode.UNIMPLEMENTED,
                    "passkey lookup by provider is not wired -- "
                    "call FindPasskey instead",
                )
                return FindCredentialByProviderResponse()

            self._set_unset(
                context,
                grpc.StatusCode.INVALID_ARGUMENT,
                "unsupported (kind, identifier) combination",
            )
            return FindCredentialByProviderResponse()
        except Exception:
            self.log.error(
                f"Error in FindCredentialByProvider: {traceback.format_exc()}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while looking up credential",
            )
            return FindCredentialByProviderResponse()

    @log_service_call()
    async def LinkCredential(
        self,
        request: LinkCredentialRequest,
        context: ServicerContext[LinkCredentialRequest, LinkCredentialResponse],
    ) -> LinkCredentialResponse:
        """Attach a new credential to an existing user."""
        try:
            if not request.user_id:
                self._set_unset(
                    context, grpc.StatusCode.INVALID_ARGUMENT, "user_id is required"
                )
                return LinkCredentialResponse()

            which = request.WhichOneof("payload")

            if (
                request.kind == CredentialKind.CREDENTIAL_KIND_DISCORD
                and which == "discord_id"
            ):
                stored = await self._service.third_parties.link_third_party(
                    request.user_id,
                    request.requester_id,
                    DiscordLink(discord_id=int(request.discord_id)),
                )
                return LinkCredentialResponse(
                    credential=stored.convert(self._to_grpc)
                )

            if (
                request.kind == CredentialKind.CREDENTIAL_KIND_GOOGLE
                and which == "google_id"
            ):
                stored = await self._service.third_parties.link_third_party(
                    request.user_id,
                    request.requester_id,
                    GoogleLink(google_id=request.google_id),
                )
                return LinkCredentialResponse(
                    credential=stored.convert(self._to_grpc)
                )

            if (
                request.kind == CredentialKind.CREDENTIAL_KIND_PASSWORD
                and which == "password_hash"
            ):
                password = await self._service.passwords.set_user_password(
                    request.user_id,
                    request.requester_id,
                    request.password_hash,
                )
                return LinkCredentialResponse(
                    credential=password.convert(self._to_grpc)
                )

            self._set_unset(
                context,
                grpc.StatusCode.INVALID_ARGUMENT,
                "unsupported (kind, payload) combination",
            )
            return LinkCredentialResponse()
        except PermissionError:
            self.log.warning(
                f"LinkCredential permission denied: requester_id={request.requester_id} "
                f"user_id={request.user_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return LinkCredentialResponse()
        except Exception:
            self.log.error(f"Error in LinkCredential: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while linking credential",
            )
            return LinkCredentialResponse()

    @log_service_call()
    async def UnlinkCredential(
        self,
        request: UnlinkCredentialRequest,
        context: ServicerContext[UnlinkCredentialRequest, Empty],
    ) -> Empty:
        """Remove a credential from a user.

        ``credential_id`` is the id of the underlying row -- for
        Discord/Google it's the ``auth.third_party.id``; the
        service walks the link row to enforce the
        ``requester_id == user_id`` check.
        """
        try:
            link = await self._service.third_parties.unlink_third_party(
                request.credential_id, request.requester_id
            )
            if not link:
                # already gone; idempotent
                return Empty()
            return Empty()
        except PermissionError:
            self.log.warning(
                f"UnlinkCredential permission denied: requester_id={request.requester_id} "
                f"credential_id={request.credential_id}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.PERMISSION_DENIED,
                "requester_id must equal user_id",
            )
            return Empty()
        except KeyError:
            self._set_unset(
                context, grpc.StatusCode.NOT_FOUND, "credential not found"
            )
            return Empty()
        except Exception:
            self.log.error(f"Error in UnlinkCredential: {traceback.format_exc()}")
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while unlinking credential",
            )
            return Empty()

    @log_service_call()
    async def ListLinkedCredentials(
        self,
        request: ListLinkedCredentialsRequest,
        context: ServicerContext[
            ListLinkedCredentialsRequest, ListLinkedCredentialsResponse
        ],
    ) -> ListLinkedCredentialsResponse:
        """List every credential linked to a user."""
        try:
            credentials: list[Credential] = []

            tp_links = await self._service.third_parties.find_third_party(
                ThirdPartyFilter(user_id=request.user_id)
            )
            for link in tp_links:
                credentials.append(link.convert(self._to_grpc))

            password = await self._service.passwords.find_password(request.user_id)
            if password is not None:
                credentials.append(password.convert(self._to_grpc))

            keys = await self._service.passkeys.list_passkeys(
                request.user_id, include_revoked=False
            )
            passkeys = [k.convert(self._to_grpc) for k in keys]

            return ListLinkedCredentialsResponse(
                credentials=credentials,
                passkeys=passkeys,
            )
        except Exception:
            self.log.error(
                f"Error in ListLinkedCredentials: {traceback.format_exc()}"
            )
            self._set_unset(
                context,
                grpc.StatusCode.INTERNAL,
                "Internal server error while listing credentials",
            )
            return ListLinkedCredentialsResponse()


__all__ = ["GrpcAuthService"]
