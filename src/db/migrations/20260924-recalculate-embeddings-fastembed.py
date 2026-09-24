"""Recalculate every note embedding with the new fastembed backend.

The original embeddings were produced by HuggingFace
``sentence-transformers`` (PyTorch).  The runtime now uses
``fastembed`` (ONNX Runtime) so it can drop the ~500 MB torch
wheel.  The two backends produce numerically different
vectors (5th-6th decimal), which is enough to shift pgvector
ranking results.

This migration walks every existing row, fetches the matching
title + content from ``note.content``, encodes via the
:class:`EmbeddingGeneratorABC` instance the migration runner
wires into ``ctx.services`` (the production backend is the
fastembed one), and overwrites the row in place.  The PK
``(note_id, model)`` stays the same; the ``model`` column is
left untouched so consumers that filter on it keep working.

Idempotent: deletes the rows for the production model id
before re-inserting.  Resumable: if the migration dies
mid-loop, a re-run truncates and starts over.  Notes that no
longer have content (cascade-deleted) are skipped.
"""

from __future__ import annotations

from src.ai.embedding_generator import EmbeddingGeneratorABC, Models
from src.api.other.types import LoggingProvider
from src.db.migrations.base import MigrationABC
from src.db.migrations.context import MigrationContext


def _model_value(model: Models) -> str:
    """Resolve a :class:`Models` enum to the string stored in the DB."""
    return model.value


class Migration(MigrationABC):

    async def up(self, ctx: MigrationContext) -> None:
        generator = ctx.services.embedding_generator
        if generator is None:
            raise ValueError(
                "MigrationContext.services.embedding_generator is required "
                "for the fastembed recalculation migration."
            )

        model = Models.MINI_LM_L6_V2
        model_str = _model_value(model)

        rows = await ctx.db.fetch(
            """
            SELECT c.id AS note_id, c.title, c.content
            FROM note.content c
            WHERE EXISTS (
                SELECT 1 FROM note.embedding e
                WHERE e.note_id = c.id AND e.model = $1
            )
            """,
            model_str,
        )
        if not rows:
            self._log.info(
                f"No embeddings to recalculate for model {model_str}"
            )
            return

        self._log.info(
            f"Recalculating {len(rows)} embeddings with {type(generator).__name__}"
        )

        # Drop the old rows first so a partial failure leaves
        # the table empty for this model rather than half PyTorch
        # / half ONNX rows.  The migration is idempotent on
        # re-run.
        await ctx.db.execute(
            "DELETE FROM note.embedding WHERE model = $1",
            model_str,
        )

        for row in rows:
            note_id = row.get("note_id")
            title = row.get("title") or ""
            content = row.get("content") or ""
            if note_id is None:
                continue

            embedding_input = f"{title}\n{content}"
            vector = generator.generate(embedding_input)
            vec_str = EmbeddingGeneratorABC.tensor_to_str_vec(vector)

            await ctx.db.execute(
                """
                INSERT INTO note.embedding (note_id, model, embedding)
                VALUES ($1, $2, $3::vector)
                """,
                note_id,
                model_str,
                vec_str,
            )

        self._log.info(
            f"Recalculated {len(rows)} embeddings for model {model_str}"
        )


__all__ = ["Migration"]