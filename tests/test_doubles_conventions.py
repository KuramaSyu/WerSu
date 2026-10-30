"""Guard rails for the test-double conventions.

Every test double must live under :mod:`tests.doubles`, grouped by its
kind (dummy / stub / spy / fake / mock / fakespy).  These tests parse the
test tree and fail if a test module defines its own double or if the
doubles package grows something that is not a known category.

See ``tests/doubles/README.md`` for the taxonomy.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parent
DOUBLES_ROOT = TESTS_ROOT / "doubles"
CATEGORIES = {"dummies", "stubs", "spys", "fakes", "mocks", "fakespys"}

# Names that mark a class as a test double rather than a domain helper.
_DOUBLE_NAME = re.compile(
    r"(Fake|Stub|Recording|InMemory|Dummy|Capturing|Raising|Noop|Mock|Spy)",
    re.IGNORECASE,
)
# Base classes that are collaborators (rather than value objects / entities).
_DOUBLE_BASE = re.compile(
    r"(ABC|Repo|Service|Facade|Database|Table|Visitor|Clock|Spawner|Handle|Provider|Client|Context)\b"
)


def _iter_test_python_files():
    for path in TESTS_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if DOUBLES_ROOT in path.parents or path.parent == DOUBLES_ROOT:
            continue
        if path.name == Path(__file__).name:
            continue
        yield path


def test_no_doubles_defined_outside_doubles_package() -> None:
    """Test modules must import doubles from ``tests.doubles`` instead of defining them."""
    offenders: list[str] = []
    for path in _iter_test_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("Test"):
                continue
            bases = [ast.unparse(base) for base in node.bases]
            is_double = bool(_DOUBLE_NAME.search(node.name))
            if not is_double:
                is_double = any(_DOUBLE_BASE.search(base) for base in bases)
            if is_double:
                offenders.append(
                    f"{path.relative_to(TESTS_ROOT)}::{node.name}"
                )
    assert not offenders, (
        "Test doubles must live under tests/doubles (grouped by kind). "
        "Move/import these: " + ", ".join(sorted(offenders))
    )


def test_doubles_are_grouped_by_category() -> None:
    """Every module under ``tests/doubles`` must sit in a known category folder."""
    assert DOUBLES_ROOT.is_dir(), "tests/doubles is missing"
    bad: list[str] = []
    for path in DOUBLES_ROOT.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(DOUBLES_ROOT)
        if len(rel.parts) < 2 or rel.parts[0] not in CATEGORIES:
            bad.append(str(rel))
    assert not bad, (
        "Every double must live in one of "
        f"{sorted(CATEGORIES)}: " + ", ".join(sorted(bad))
    )
