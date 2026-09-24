from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.api.other.types import LoggingProvider
from src.api.services.background_process.background_process import (
    BackgroundProcessABC,
)
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.database import DatabaseABC
from src.db.repos.note.embedding import NoteEmbeddingRepo


class MissingEmbeddingProcessImpl(BackgroundProcessABC):
    """One-shot backfill of embeddings for the wired model on startup, 
    if any embeddings for notes where missing."""

    def __init__(
        self,
        db: DatabaseABC,
        embedding_repo: NoteEmbeddingRepo,
        log: LoggingProvider,
        batch_limit: int = 1000,
    ) -> None:
        self._db = db
        self._embedding_repo = embedding_repo
        self._log = log(__name__, self)
        self._batch_limit = batch_limit
        self._has_run = False
        self._handle: Optional[SchedulerHandleABC] = None

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        """One-shot: never run again after the first sweep."""
        if self._has_run:
            return None
        # ``now`` triggers the scheduler's immediate-idle path on
        # the first tick, so the sweep runs right after start().
        return datetime.now()

    async def run(self) -> None:
        self._has_run = True
        model = self._embedding_repo.embedding_generator.model_name
        self._log.info(f"MissingEmbeddingProcess: sweeping for model={model}")

        try:
            missing = await self._db.fetch(
                """
                SELECT c.id AS note_id, c.title, c.content
                FROM note.content c
                LEFT JOIN note.embedding e
                    ON e.note_id = c.id AND e.model = $1
                WHERE e.note_id IS NULL
                LIMIT $2
                """,
                model,
                self._batch_limit,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.exception(
                f"MissingEmbeddingProcess: failed to scan note.content: {exc!r}"
            )
            return

        if not missing:
            self._log.info("MissingEmbeddingProcess: nothing to backfill")
            return

        self._log.info(
            f"MissingEmbeddingProcess: encoding {len(missing)} note(s) for model={model}"
        )

        encoded = 0
        for row in missing:
            note_id = row.get("note_id")
            if note_id is None:
                continue
            title = row.get("title") or ""
            content = row.get("content") or ""
            try:
                await self._embedding_repo.insert(note_id, title, content)
                encoded += 1
            except Exception as exc:  # noqa: BLE001
                self._log.exception(
                    f"MissingEmbeddingProcess: insert failed for "
                    f"note_id={note_id}: {exc!r}"
                )

        self._log.info(
            f"MissingEmbeddingProcess: encoded {encoded}/{len(missing)} "
            f"note(s) for model={model}"
        )