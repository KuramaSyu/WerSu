"""Unified-diff helpers shared by note-version reads and compaction.

The patch format is a plain ``difflib.unified_diff`` with a small
context window.  Both the repository (when reconstructing a version)
and the compactor (when computing a day's final state) need the same
build/apply semantics, so they live here instead of on the repo.
"""

from __future__ import annotations

import difflib


def build_patch(old_text: str, new_text: str) -> str:
    """Return a unified diff that turns ``old_text`` into ``new_text``.

    ``unified_diff`` is O(N) plus hunk headers and far smaller than
    ``ndiff`` for large, mostly-identical content.  ``n=2`` keeps a
    small context window so the patch stays self-describing.
    """
    old_payload = old_text if old_text.endswith("\n") else f"{old_text}\n"
    new_payload = new_text if new_text.endswith("\n") else f"{new_text}\n"
    diff_lines = difflib.unified_diff(
        old_payload.splitlines(keepends=True),
        new_payload.splitlines(keepends=True),
        n=2,
    )
    return "".join(diff_lines)


def apply_patch(patch_text: str, base_text: str) -> str:
    """Apply a unified diff produced by :func:`build_patch`.

    The patch only contains the changed hunks plus a few context lines,
    so the untouched regions of ``base_text`` must be copied around
    each hunk using the ``@@ -old_start,old_count`` offsets.
    Concatenating the payload lines alone loses everything outside the
    hunks (leading sections, table headers, trailing text, ...).
    """
    if not patch_text:
        return base_text

    # build_patch normalises both sides to a trailing newline before
    # diffing, so hunk offsets assume a newline-terminated payload.
    # Mirror that for the base or the offsets drift on content saved
    # without a final newline.
    needs_trim = not base_text.endswith("\n")
    base_lines = base_text.splitlines(keepends=True)
    if needs_trim and base_lines:
        base_lines[-1] += "\n"

    restored: list[str] = []
    cursor = 0  # next base line that has not been consumed yet

    for line in patch_text.splitlines(keepends=True):
        # File headers are written as "--- " / "+++ " (empty file
        # names). Content lines always carry a prefix, so a line
        # starting with exactly these markers cannot be payload.
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if line.startswith("@@"):
            # @@ -old_start[,old_count] +new_start[,new_count] @@
            header = line[2:].split("@@", 1)[0].strip()
            old_spec = header.split(" ", 1)[0].lstrip("-")
            start_text, _, count_text = old_spec.partition(",")
            old_start = int(start_text)
            old_count = int(count_text) if count_text else 1
            # Copy everything before this hunk from the base. An
            # insertion-only hunk (old_count == 0) points *after* the
            # line it inserts at.
            target = old_start - 1 if old_count > 0 else old_start
            if target > cursor:
                restored.extend(base_lines[cursor:target])
                cursor = target
            continue

        prefix = line[:1]
        if prefix == " ":
            # Context line: identical in base and result. Append the
            # payload (not the base line) and consume one old line.
            restored.append(line[1:])
            cursor += 1
        elif prefix == "-":
            cursor += 1
        elif prefix == "+":
            restored.append(line[1:])
        # "\\ No newline at end of file" and other markers carry no
        # payload and are ignored.

    restored.extend(base_lines[cursor:])
    result = "".join(restored)
    if needs_trim and result.endswith("\n"):
        result = result[:-1]
    return result


__all__ = ["build_patch", "apply_patch"]
