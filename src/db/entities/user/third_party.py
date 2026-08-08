"""One linked third-party provider per user."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api.other.visitor import AcceptsVisitor, EntityVisitor


@dataclass
class ThirdPartyEntity(AcceptsVisitor):
    """One linked third-party provider per user.

    Provider-specific extras (e.g. Discord's 4-digit
    ``discriminator``) live in :attr:`extra_fields` -- a JSON
    column.  Use :meth:`get_extra` / :meth:`set_extra` /
    :attr:`serialised_extras` rather than indexing the dict
    directly so the JSON serialisation stays consistent.
    """

    id: UndefinedOr[str] = UNDEFINED
    user_id: str = ""
    provider: str = ""
    provider_user_id: str = ""
    extra_fields: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[_dt.datetime] = None

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Return ``extra_fields[key]`` if present, else `default`."""
        return self.extra_fields.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        """Set ``extra_fields[key] = value``."""
        self.extra_fields[key] = value

    @property
    def serialised_extras(self) -> Optional[str]:
        """JSON string of :attr:`extra_fields`, or ``None`` when empty."""
        if not self.extra_fields:
            return None
        return json.dumps(self.extra_fields, default=str)

    def visit(self, visitor: EntityVisitor) -> Any:
        """Dispatch this third-party link to ``visitor.visit_third_party``."""
        return visitor.visit_third_party(self)


@dataclass(frozen=True)
class ThirdPartyFilter:
    """Filter for "find me a third-party link with these properties".

    Every field is :data:`UndefinedOr` so callers can leave fields
    unset.  Set fields are AND-ed -- the filter matches a link
    only when every set field equals the link's value for that
    field.  Frozen so the filter is hashable.

    Attributes:
        id: target link's server-assigned id.
        user_id: target link's owning user.
        provider: target link's provider (``"discord"``, ``"google"``).
        provider_user_id: target link's provider-side user id.
    """

    id: UndefinedOr[str] = UNDEFINED
    user_id: UndefinedOr[str] = UNDEFINED
    provider: UndefinedOr[str] = UNDEFINED
    provider_user_id: UndefinedOr[str] = UNDEFINED

    def is_empty(self) -> bool:
        """Return ``True`` when no field is set.

        ``find_third_party`` returns ``[]`` for an empty filter by
        design -- an unfiltered lookup would have to scan every
        link row.
        """
        return (
            is_undefined(self.id)
            and is_undefined(self.user_id)
            and is_undefined(self.provider)
            and is_undefined(self.provider_user_id)
        )

    def set_fields(self) -> List[str]:
        """Return the names of the fields that are currently set."""
        out: List[str] = []
        if not is_undefined(self.id):
            out.append("id")
        if not is_undefined(self.user_id):
            out.append("user_id")
        if not is_undefined(self.provider):
            out.append("provider")
        if not is_undefined(self.provider_user_id):
            out.append("provider_user_id")
        return out


@dataclass(frozen=True)
class DiscordLink:
    """Typed payload for linking a Discord account.

    ``discord_id`` is the numeric Discord user id.
    ``discriminator`` is the optional 4-digit Discord tag
    (``"1234"``); OAuth signups that don't have one leave it
    unset so it serialises as ``None`` in ``extra_fields``.
    """

    discord_id: int
    discriminator: Optional[str] = None


@dataclass(frozen=True)
class GoogleLink:
    """Typed payload for linking a Google account.

    ``google_id`` is the stable user id from Google's ``sub``
    claim -- the OAuth controller hands it over verbatim after
    verifying the id_token.
    """

    google_id: str


# Either typed payload can be passed to
# :meth:`UserThirdPartyAuthServiceABC.link_third_party`.
ThirdPartyLinkSpec = DiscordLink | GoogleLink


__all__ = [
    "DiscordLink",
    "GoogleLink",
    "ThirdPartyEntity",
    "ThirdPartyFilter",
    "ThirdPartyLinkSpec",
]
