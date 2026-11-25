"""LangGraph checkpoint rows (sealed by ``agent.checkpoint``)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from moodle_llm_chat.index.store.base import Repository


class CheckpointRepo(Repository):
    """LangGraph checkpoint rows (sealed by ``agent.checkpoint``)."""

    def ckpt_rows(self, thread_id: str) -> tuple[list[Any], list[Any], list[Any]]:
        con = self.connection
        return (
            con.execute("SELECT * FROM ckpt_storage WHERE thread_id=?", (thread_id,)).fetchall(),
            con.execute("SELECT * FROM ckpt_writes WHERE thread_id=?", (thread_id,)).fetchall(),
            con.execute("SELECT * FROM ckpt_blobs WHERE thread_id=?", (thread_id,)).fetchall(),
        )

    def ckpt_put(
        self,
        *,
        storage: Sequence[tuple[str, str, str, bytes]] = (),
        writes: Sequence[tuple[str, str, str, str, int, bytes]] = (),
        blobs: Sequence[tuple[str, str, str, str, bytes]] = (),
    ) -> None:
        with self.connection as con:
            con.executemany("INSERT OR REPLACE INTO ckpt_storage VALUES (?,?,?,?)", storage)
            con.executemany("INSERT OR REPLACE INTO ckpt_writes VALUES (?,?,?,?,?,?)", writes)
            con.executemany("INSERT OR REPLACE INTO ckpt_blobs VALUES (?,?,?,?,?)", blobs)

    def ckpt_prune(
        self,
        thread_id: str,
        *,
        checkpoints: Sequence[tuple[str, str]],
        blobs: Sequence[tuple[str, str, str]],
    ) -> None:
        """Drop the given ``(ns, checkpoint_id)`` checkpoints, their writes, and blobs."""
        with self.connection as con:
            for ns, cid in checkpoints:
                for table in ("ckpt_storage", "ckpt_writes"):
                    con.execute(
                        f"DELETE FROM {table} WHERE thread_id=? AND ns=? AND checkpoint_id=?",
                        (thread_id, ns, cid),
                    )
            con.executemany(
                "DELETE FROM ckpt_blobs WHERE thread_id=? AND ns=? AND channel=? AND version=?",
                [(thread_id, *blob) for blob in blobs],
            )

    def ckpt_delete(self, thread_id: str) -> None:
        with self.connection as con:
            for table in ("ckpt_storage", "ckpt_writes", "ckpt_blobs"):
                con.execute(f"DELETE FROM {table} WHERE thread_id=?", (thread_id,))
