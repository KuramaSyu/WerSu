"""Create the ``rules`` table.

Mirrors the schema in ``rules.sql`` (kept for documentation) --
this migration file is the source of truth that the runner picks
up at startup.  Single ``CREATE TABLE IF NOT EXISTS`` plus the
supporting indexes; the table layout is documented on
:class:`src.db.entities.rule.RuleEntity`.

Why a dedicated table rather than storing rules on the existing
``activity`` table: rules are mutable, addressable entities
(``id`` PK, ``enabled`` flag, ``creator_id`` audit) -- the
activity table is append-only and shaped around one-shot
events.  Mixing the two would force one shape to compromise
the other.
"""

from __future__ import annotations

from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


class Migration(MigrationABC):
    """Create the ``rules`` table + its supporting indexes."""

    async def up(self, ctx: MigrationContext) -> None:
        """Apply the rules schema in a single execute call.

        All statements run inside the implicit per-call transaction
        provided by :class:`src.db.database.acquire`, matching the
        convention used by every other migration.
        """
        await ctx.db.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                event_type            text NOT NULL,
                attached_entity_type  text,
                attached_entity_id    text,
                condition             jsonb NOT NULL,
                action_type           text NOT NULL,
                action_context        jsonb NOT NULL,
                enabled               boolean NOT NULL DEFAULT true,
                creator_id            text NOT NULL,
                created_at            timestamptz NOT NULL DEFAULT now(),
                updated_at            timestamptz NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS rules_event_enabled_idx
                ON rules (event_type, enabled);

            CREATE INDEX IF NOT EXISTS rules_attached_entity_idx
                ON rules (attached_entity_type, attached_entity_id);

            CREATE INDEX IF NOT EXISTS rules_action_type_idx
                ON rules (action_type);

            CREATE INDEX IF NOT EXISTS rules_creator_idx
                ON rules (creator_id);
            """
        )
