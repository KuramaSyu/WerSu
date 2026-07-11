"""Parse a directory ``README.md`` note into its displayable parts.

A directory README is expected to have this layout:

    # title
    ![directory_image](url)
    description

Any of the three parts may be missing or in a different order, in
which case the corresponding slot is left empty.  The parser is
deliberately tolerant: it never raises, so callers can safely feed it
untrusted user content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


README_TITLE = "README.md"
"""Canonical title of a directory's README note.

Shared by :mod:`src.services.directory` and
:mod:`src.services.note` so both layers agree on the title that
counts as a README binding.
"""


# Match the *first* level-1 heading in the body: ``# title``.
_TITLE_RE = re.compile(r"^\s*#\s+(?P<title>.+?)\s*$", re.MULTILINE)

# Match the *first* markdown image: ``![alt](url)``.  ``alt`` may
# contain anything except ``]``; ``url`` is captured verbatim.
_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?P<url>[^)\s]+)\)",
)


@dataclass(frozen=True)
class ParsedReadme:
    """Extracted pieces of a directory README.

    Attributes
    ----------
    title : Optional[str]
        Heading text from the first ``# ...`` line, with whitespace
        trimmed.  ``None`` when no heading is present.
    image_url : Optional[str]
        URL from the first ``![...](url)`` image.  ``None`` when no
        image is present.
    description : str
        Body text after the title and image, with leading and trailing
        whitespace stripped.  Empty string when there is nothing left.
    """

    title: Optional[str]
    image_url: Optional[str]
    description: str

    
    def write_readme(self) -> str:
        """Write a README note for the directory.

        Args:
            content: raw README body (may be empty).
        """
        lines: list[str] = []
        if self.title:
            lines.append(f"# {self.title}")
        if self.image_url:
            lines.append(f"![alt]({self.image_url})")
        if self.description:
            lines.append(self.description)
        return "\n\n".join(lines)


def parse_readme(content: Optional[str]) -> ParsedReadme:
    """Parse ``content`` into title, image URL, and description.

    Args:
        content: raw README body (may be :obj:`None` or empty).

    Returns:
        :class:`ParsedReadme`: parsed pieces; any missing slot is
        ``None`` / empty.
    """
    if not content:
        return ParsedReadme(title=None, image_url=None, description="")

    title: Optional[str] = None
    image_url: Optional[str] = None

    title_match = _TITLE_RE.search(content)
    if title_match is not None:
        title = title_match.group("title").strip() or None

    image_match = _IMAGE_RE.search(content)
    if image_match is not None:
        image_url = image_match.group("url").strip() or None

    # Description = every line that is neither the title nor the image
    # line.  We drop each consumed line by rewriting it to an empty
    # line so the remaining layout stays predictable.
    consumed: list[tuple[int, int]] = []
    if title_match is not None:
        consumed.append((title_match.start(), title_match.end()))
    if image_match is not None:
        consumed.append((image_match.start(), image_match.end()))

    if consumed:
        consumed.sort()
        rebuilt = list(content)
        for start, end in consumed:
            for i in range(start, end):
                rebuilt[i] = " "
        remaining = "".join(rebuilt)
    else:
        remaining = content

    description = "\n".join(
        line.strip() for line in remaining.splitlines() if line.strip()
    ).strip()

    return ParsedReadme(
        title=title,
        image_url=image_url,
        description=description,
    )


        

__all__ = ["ParsedReadme", "parse_readme"]
