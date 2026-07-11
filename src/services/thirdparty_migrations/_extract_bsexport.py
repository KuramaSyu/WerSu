"""BookStack-specific attachment id extractor.

This is the BookStack importer's private counterpart to
:func:`src.utils.extract_attachments.extract_attachment_ids`.  It is
intentionally separate because the two functions look at very
different shapes of content:

* :func:`extract_attachment_ids` is the project-wide helper that
  scans note bodies for canonical attachment URLs
  (``/api/attachments/...`` and ``?key=...``).  It deliberately
  requires ``key=`` to be preceded by ``?`` or ``&`` so that
  in-prose text like ``key=123` is not mistaken for an attachment
  reference.

* :func:`extract_bookstack_attachment_ids` walks the BookStack
  export shapes specifically:

      - ``[[bsexport:image:N]]`` and ``[[bsexport:attachment:N]]``
        cross-refs in plain prose.
      - the backslash-escaped variant
        ``\\[[bsexport:image:N]\\]`` that :mod:`html2text` emits
        when an ``<img src="[[bsexport:...]]">`` attribute value
        carries brackets.

  Only ``image`` and ``attachment`` kinds are returned -- the
  importer never creates attachment rows for ``page``, ``chapter``
  or ``book`` cross-refs.

The return value is the list of source ids (still in BookStack's
numeric form) so the caller can resolve them through the
``id_index`` map produced by :class:`BookstackBookImport`.

Args:
    content: raw note body (may be markdown or plain text).

Returns:
    List[int]: unique numeric ids, in the order they first appear.
"""

from __future__ import annotations

import re
from typing import List


# Match `[[bsexport:kind:id]]` in both the plain and the
# backslash-escaped form.  Each `[\\]?` lets the regex accept an
# optional leading backslash so html2text's escaped variant is also
# picked up.  The list of kinds mirrors the BookStack export schema
# -- we filter to image / attachment below because those are the
# only kinds we ever create attachment rows for.
_BSEXPORT_RE = re.compile(
    r"[\\]?\["
    r"[\\]?\["
    r"bsexport:"
    r"(?P<kind>image|attachment|page|chapter|book)"
    r":(?P<id>\d+)"
    r"[\\]?\]"
    r"[\\]?\]"
)

_KINDS_WE_LINK = frozenset({"image", "attachment"})


def extract_bookstack_attachment_ids(content: str) -> List[int]:
    if not content:
        return []

    seen: set[int] = set()
    result: List[int] = []
    for match in _BSEXPORT_RE.finditer(content):
        if match.group("kind") not in _KINDS_WE_LINK:
            continue
        try:
            source_id = int(match.group("id"))
        except ValueError:
            continue
        if source_id in seen:
            continue
        seen.add(source_id)
        result.append(source_id)
    return result


__all__ = ["extract_bookstack_attachment_ids"]