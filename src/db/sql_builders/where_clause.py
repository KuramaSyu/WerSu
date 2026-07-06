"""WhereClause - composable WHERE clause with an AND group and an OR group.

Two parallel groups of *pairs* joined separately and combined with
``AND``; the OR group is parenthesised so e.g.::

    WhereClause.build(and={"status": "active"}, or={"region": "EU", "country": "DE"})

emits ``WHERE (status = $1) AND (region = $2 OR country = $3)``.

Each pair is one of:

* ``(column, value)`` -- emits ``column = $N`` and binds ``value``.
  This is the plain equality form, matching the original two-group
  design.
* ``(raw_sql, value)`` -- emits ``raw_sql`` with a single ``$N``
  placeholder (Postgres) or ``?`` (SQLite) and binds ``value``.
  Use this when you need anything more exotic than ``=``: ``= ANY($N)``,
  ``>= $N``, ``LIKE $N``, ...  ``raw_sql`` may itself contain ``$N``
  references; they're resolved by the per-statement placeholder
  counter.

A pair with a ``None`` value in the plain form is rendered as
``column IS NULL`` (still consuming no bound parameter).  The
three-tuple form ``(column, None, False)`` forces ``column = NULL``
which is never true; this exists for completeness but rarely useful.

Constrained to one AND group + one OR group; anything more exotic
should fall through to :meth:`SqlBuilderABC.fetch`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Sequence, Tuple, Union


# A pair is either (column_or_sql, value) -> ``column = $N`` (or
# ``IS NULL`` if value is ``None``) or (column_or_sql, value, is_null)
# which forces the IS NULL vs equality rendering explicitly.
WherePair = Union[Tuple[str, object], Tuple[str, object, bool]]
"""A single ``(column_or_sql, value)`` pair.

The optional third element (``bool``) selects between ``column = $N``
(False, default) and ``column IS NULL`` (True) when ``value`` is
``None``.  When ``value`` is not ``None`` the third element is
ignored.
"""


@dataclass
class WhereClause:
    """AND/OR WHERE clause.

    Attributes:
        and_pairs: pairs joined by ``AND``.
        or_pairs: pairs joined by ``OR``.
        raw_and: raw-SQL predicates (no bound parameters) joined
            by ``AND``.  Use :meth:`add_raw` to push a predicate
            that doesn't fit the pair model (e.g. ``"note_id IS
            NOT NULL"``).
        raw_or: raw-SQL predicates joined by ``OR``.

    Note:
        Both pair groups are tuples (not dicts) so the same column
        may legitimately appear with two different values.  The
        :meth:`build` factory accepts a dict for convenience; that
        collapses duplicate keys inside one group.  Construct the
        class directly if you need repeats or any non-equality
        operator.  Use :meth:`add_and` / :meth:`add_or` /
        :meth:`add_raw` to grow an existing clause incrementally
        -- the data flow used by ``PostgresActivityRepo`` when
        assembling many optional filters one at a time.
    """

    and_pairs: Tuple[WherePair, ...] = ()
    or_pairs: Tuple[WherePair, ...] = ()
    raw_and: Tuple[str, ...] = ()
    raw_or: Tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        and_: dict | None = None,
        or_: dict | None = None,
        raw_and: Sequence[str] | None = None,
        raw_or: Sequence[str] | None = None,
    ) -> "WhereClause":
        """Build a clause from two optional dicts.

        Args:
            and_: pairs joined by ``AND``.  ``None`` => no AND group.
            or_: pairs joined by ``OR``.  ``None`` => no OR group.
            raw_and: raw-SQL predicates joined by ``AND``.
            raw_or: raw-SQL predicates joined by ``OR``.
        """
        return cls(
            and_pairs=tuple((and_ or {}).items()),
            or_pairs=tuple((or_ or {}).items()),
            raw_and=tuple(raw_and or ()),
            raw_or=tuple(raw_or or ()),
        )

    @classmethod
    def empty(cls) -> "WhereClause":
        """Return an empty :class:`WhereClause` (no AND, no OR group)."""
        return cls()

    @property
    def is_empty(self) -> bool:
        """``True`` when every group is empty."""
        return (
            not self.and_pairs
            and not self.or_pairs
            and not self.raw_and
            and not self.raw_or
        )

    def add_and(self, *pairs: WherePair) -> "WhereClause":
        """Return a new clause with ``pairs`` appended to the AND group.

        Returns a fresh instance (the dataclass is treated as
        immutable); the existing instance is left untouched.
        """
        return WhereClause(
            and_pairs=self.and_pairs + tuple(pairs),
            or_pairs=self.or_pairs,
            raw_and=self.raw_and,
            raw_or=self.raw_or,
        )

    def add_or(self, *pairs: WherePair) -> "WhereClause":
        """Return a new clause with ``pairs`` appended to the OR group.

        Returns a fresh instance; the existing instance is left
        untouched.
        """
        return WhereClause(
            and_pairs=self.and_pairs,
            or_pairs=self.or_pairs + tuple(pairs),
            raw_and=self.raw_and,
            raw_or=self.raw_or,
        )

    def add_raw(self, predicate: str, *, group: Literal["and", "or"] = "and") -> "WhereClause":
        """Append a parameter-free raw-SQL predicate.

        Use this for predicates the (column, value) pair model
        can't express, e.g. ``"note_id IS NOT NULL"``.  Predicates
        must not contain placeholders (``$N`` / ``?``); if they do
        it's a programmer error because the rendered WHERE clause
        would never bind them.

        Args:
            predicate: SQL fragment to splice into the WHERE.
                Must not contain ``$N`` or ``?`` placeholders.
            group: which group to append to -- ``"and"`` (default)
                or ``"or"``.

        Returns:
            WhereClause: a fresh instance; the original is
            unchanged.

        Raises:
            ValueError: if ``predicate`` looks like it binds a
                parameter (it would render with no placeholder
                and silently drop the value).
        """
        if "$" in predicate or "?" in predicate:
            raise ValueError(
                "WhereClause.add_raw only accepts parameter-free "
                "predicates; bind values via add_and/add_or pairs"
            )
        if group == "or":
            return WhereClause(
                and_pairs=self.and_pairs,
                or_pairs=self.or_pairs,
                raw_and=self.raw_and,
                raw_or=self.raw_or + (predicate,),
            )
        return WhereClause(
            and_pairs=self.and_pairs,
            or_pairs=self.or_pairs,
            raw_and=self.raw_and + (predicate,),
            raw_or=self.raw_or,
        )

    def __add__(self, other: "WhereClause") -> "WhereClause":
        """Return ``self + other`` -- merge two clauses pair-by-pair.

        The AND groups of both sides are concatenated, the OR
        groups of both sides are concatenated, the raw predicate
        lists of both sides are concatenated, and the whole
        combined object lands in the AND group when rendered.
        Empty operands drop out, so ``clause + WhereClause.empty()``
        is just ``clause``.

        Returns:
            WhereClause: a fresh clause combining every pair and
            raw predicate from both sides; neither operand is
            mutated.

        Returns NotImplemented for non-:class:`WhereClause` so
        Python falls back to the right-hand operand's own
        ``__radd__`` / raises ``TypeError`` as usual.
        """
        if not isinstance(other, WhereClause):
            return NotImplemented
        return WhereClause(
            and_pairs=self.and_pairs + other.and_pairs,
            or_pairs=self.or_pairs + other.or_pairs,
            raw_and=self.raw_and + other.raw_and,
            raw_or=self.raw_or + other.raw_or,
        )

    def __radd__(self, other: "WhereClause") -> "WhereClause":
        """Mirror :meth:`__add__` so ``empty() + clause`` also works."""
        return self.__add__(other)

    def total_params(self) -> int:
        """Number of bound values this clause consumes.

        Pairs whose value is ``None`` (and that aren't explicitly
        forced to ``= NULL``) render as ``IS NULL`` and consume no
        parameter.
        """
        total = 0
        for pair in self.and_pairs:
            total += _consumes_param(pair)
        for pair in self.or_pairs:
            total += _consumes_param(pair)
        return total

    def all_pairs(self) -> Iterable[WherePair]:
        """Yield every pair in AND-then-OR order."""
        yield from self.and_pairs
        yield from self.or_pairs


def _consumes_param(pair: WherePair) -> int:
    """Whether this pair binds a parameter."""
    _col, value, *rest = pair
    if rest and rest[0]:
        # explicit IS NULL form -- no param
        return 0
    if value is None:
        # plain (col, None) defaults to IS NULL -- no param
        return 0
    return 1


def is_is_null_pair(pair: WherePair) -> bool:
    """``True`` when ``pair`` should render as ``column IS NULL``."""
    _col, value, *rest = pair
    if rest:
        return bool(rest[0])
    return value is None


def is_raw_pair(pair: WherePair) -> bool:
    """``True`` when ``pair`` uses a raw-SQL fragment.

    A raw pair is detected by a ``$N`` or ``?`` placeholder inside
    the column slot.  Plain equalities don't carry placeholders.
    """
    col, _value, *_rest = pair
    return "$" in col or "?" in col


def render_where(
    where: "WhereClause",
    *,
    dialect: str,
    start_n: int = 1,
) -> Tuple[str, Tuple[object, ...]]:
    """Render a :class:`WhereClause` to ``(sql, params)`` for one dialect.

    The AND group is rendered first, then the OR group parenthesised,
    joined by ``AND`` -- same shape every staged builder emits.  Use
    this when you've built a :class:`WhereClause` outside the
    staged-builder pipeline (e.g. an aggregate query that needs a
    trailing ``GROUP BY`` the builder doesn't know about) and want
    the same per-dialect placeholder rules as the rest of the code.

    Args:
        where: the clause to render.  An empty clause produces
            ``("", ())``.
        dialect: ``"postgres"`` or ``"sqlite"``.  Postgres uses
            numbered ``$N`` placeholders starting at ``start_n``;
            SQLite uses bare ``?`` and ignores ``start_n``.
        start_n: first placeholder number for ``"postgres"``.

    Returns:
        Tuple ``(sql_fragment, bound_params)``.  ``sql_fragment``
        has no leading ``WHERE`` keyword; prepend it yourself when
        needed.

    Pair shapes
    -----------

    Each pair is one of:

    * ``(column, value)`` -- ``column = $N`` (or ``column = ?``).
    * ``(column, None)`` -- ``column IS NULL`` (no bound param).
    * ``(column, None, True)`` -- ``column IS NULL`` (explicit).
    * ``(column, None, False)`` -- ``column = NULL`` (always false;
      rarely useful).
    * ``(raw_sql, value)`` -- ``raw_sql`` with one placeholder.
    * ``(column, [v1, v2, ...])`` -- ``column IN ($N, $N+1, ...)``
      with each value bound separately.  Empty list -> ``FALSE``
      (matches no rows).  Both dialects support this natively.
    """
    if where.is_empty:
        return "", ()

    style = "$" if dialect == "postgres" else "?"

    def _emit(pair: WherePair, n: int) -> Tuple[str, int, list[object]]:
        col, value, *rest = pair
        force_is_null = bool(rest[0]) if rest else False

        # IS NULL paths consume no bound param.
        if value is None and not rest:
            return f"{col} IS NULL", n, []
        if value is None and rest and force_is_null:
            return f"{col} IS NULL", n, []
        if value is None and rest and not force_is_null:
            ph = f"${n}" if style == "$" else "?"
            return f"{col} = {ph}", n + (1 if style == "$" else 0), [None]

        # List values render as ``column IN ($N, $N+1, ...)`` so both
        # dialects accept the same shape.  Postgres bumps ``n`` per
        # placeholder; SQLite doesn't care about ``n`` so the value
        # passed through is harmless.
        if isinstance(value, (list, tuple)):
            items = list(value)
            if not items:
                return "FALSE", n, []
            if style == "$":
                phs = [f"${n + i}" for i in range(len(items))]
                next_n = n + len(items)
            else:
                phs = ["?"] * len(items)
                next_n = n
            rendered = f"{col} IN ({', '.join(phs)})"
            return rendered, next_n, list(items)

        if is_raw_pair(pair):
            ph = f"${n}" if style == "$" else "?"
            rendered = col.replace("$N", ph).replace("?", ph)
            return rendered, n + (1 if style == "$" else 0), [value]

        ph = f"${n}" if style == "$" else "?"
        return f"{col} = {ph}", n + (1 if style == "$" else 0), [value]

    parts: list[str] = []
    params: list[object] = []
    n = start_n

    if where.and_pairs or where.raw_and:
        and_subs: list[str] = []
        for pair in where.and_pairs:
            sub, n, sub_params = _emit(pair, n)
            and_subs.append(sub)
            params.extend(sub_params)
        and_subs.extend(where.raw_and)
        parts.append(f"({' AND '.join(and_subs)})")

    if where.or_pairs or where.raw_or:
        or_subs: list[str] = []
        for pair in where.or_pairs:
            sub, n, sub_params = _emit(pair, n)
            or_subs.append(sub)
            params.extend(sub_params)
        or_subs.extend(where.raw_or)
        parts.append(f"({' OR '.join(or_subs)})")

    return " AND ".join(parts), tuple(params)