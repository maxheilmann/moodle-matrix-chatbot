"""Conversations, their event log and turns, all sealed with the owner's chat key."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from moodle_llm_chat.crypto.envelope import aad, open_box, seal
from moodle_llm_chat.crypto.keys import Scope
from moodle_llm_chat.index.models import Conversation, Turn
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError
from moodle_llm_chat.index.store.helpers import resolve_now

if TYPE_CHECKING:
    from moodle_llm_chat.index.store.core import Store

#: Event seqs are reserved in blocks of this size, so allocating one is rarely a write.
SEQ_BLOCK = 256


def _unseen_part(event: dict[str, Any], after_seq: int) -> dict[str, Any] | None:
    """What a client that has seen up to ``after_seq`` has not seen of a stored event.

    Only merged streamed runs (they carry ``parts``) can be partly seen: their deltas went
    out live as the consecutive seqs ending at the run's own ``seq``. ``parts`` itself is
    storage bookkeeping and is never returned.
    """
    parts = event.pop("parts", None)
    if parts is None:
        return event
    seen = after_seq - (int(event["seq"]) - len(parts))
    if seen <= 0:
        return event
    delta = event.get("delta")
    rest = delta[sum(parts[:seen]) :] if isinstance(delta, str) else ""
    return dict(event, delta=rest) if rest else None


class ChatRepo(Repository):
    """Conversations, their event log and turns, all sealed with the owner's chat key."""

    def __init__(self, db: Store) -> None:
        super().__init__(db)
        #: conversation id -> (next event seq, first seq not reserved yet)
        self._event_seqs: dict[str, tuple[int, int]] = {}

    def chat_dek(self, site_id: str, user_id: int) -> bytes:
        return self.ring.dek(self._db, Scope.CHAT, f"{site_id}:{user_id}")

    def create_conversation(
        self,
        site_id: str,
        user_id: int,
        *,
        course_id: int | None = None,
        now: int | None = None,
    ) -> str:
        conv_id = secrets.token_urlsafe(12)
        stamp = resolve_now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO conversations (id, site_id, user_id, course_id, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?)",
                (conv_id, site_id, user_id, course_id, stamp, stamp),
            )
        return conv_id

    def _conversation_row(self, site_id: str, user_id: int, conv_id: str) -> Any:
        row = self.connection.execute(
            "SELECT * FROM conversations WHERE id=? AND site_id=? AND user_id=? "
            "AND archived_at IS NULL",
            (conv_id, site_id, user_id),
        ).fetchone()
        if row is None:
            raise StoreError("no such conversation")
        return row

    def _to_conversation(self, row: Any) -> Conversation:
        title = ""
        if row["sealed_title"] is not None:
            title = open_box(
                self.chat_dek(row["site_id"], row["user_id"]),
                bytes(row["sealed_title"]),
                aad=aad("conversations", row["id"], "title"),
            )
        return Conversation(
            id=row["id"],
            site_id=row["site_id"],
            user_id=row["user_id"],
            course_id=row["course_id"],
            title=title,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def conversation(self, site_id: str, user_id: int, conv_id: str) -> Conversation:
        return self._to_conversation(self._conversation_row(site_id, user_id, conv_id))

    def conversations(self, site_id: str, user_id: int, *, limit: int = 100) -> list[Conversation]:
        rows = self.connection.execute(
            "SELECT * FROM conversations WHERE site_id=? AND user_id=? AND archived_at IS NULL "
            "ORDER BY updated_at DESC, created_at DESC LIMIT ?",
            (site_id, user_id, limit),
        ).fetchall()
        return [self._to_conversation(r) for r in rows]

    def set_conversation_title(self, site_id: str, user_id: int, conv_id: str, title: str) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        sealed = seal(
            self.chat_dek(site_id, user_id), title[:200], aad=aad("conversations", conv_id, "title")
        )
        with self.connection as con:
            con.execute("UPDATE conversations SET sealed_title=? WHERE id=?", (sealed, conv_id))

    def archive_conversation(
        self, site_id: str, user_id: int, conv_id: str, *, now: int | None = None
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        with self.connection as con:
            con.execute(
                "UPDATE conversations SET archived_at=? WHERE id=?", (resolve_now(now), conv_id)
            )

    def save_citations(
        self, site_id: str, user_id: int, conv_id: str, refs: list[dict[str, Any]]
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        sealed = self._seal_json(
            self.chat_dek(site_id, user_id), refs, bound_to=aad("conversations", conv_id, "cites")
        )
        with self.connection as con:
            con.execute("UPDATE conversations SET sealed_citations=? WHERE id=?", (sealed, conv_id))

    def citations(self, site_id: str, user_id: int, conv_id: str) -> list[dict[str, Any]]:
        row = self._conversation_row(site_id, user_id, conv_id)
        if row["sealed_citations"] is None:
            return []
        refs: list[dict[str, Any]] = self._open_json(
            self.chat_dek(site_id, user_id),
            row["sealed_citations"],
            bound_to=aad("conversations", conv_id, "cites"),
        )
        return refs

    def next_seq(self, conv_id: str) -> int:
        """Allocate the next event seq of a conversation.

        Seqs go out to live clients before (or without) the event being stored, so they
        must never be reused, not even after a crash. They are reserved
        :data:`SEQ_BLOCK` at a time; after a restart allocation resumes past the whole
        last reservation. Gaps in the stored log are expected.
        """
        cached = self._event_seqs.get(conv_id)
        if cached is None:
            con = self.connection
            row = con.execute(
                "SELECT hi FROM conversation_seq WHERE conversation_id=?", (conv_id,)
            ).fetchone()
            stored = con.execute(
                "SELECT coalesce(max(seq), 0) + 1 FROM turn_events WHERE conversation_id=?",
                (conv_id,),
            ).fetchone()[0]
            start = max(int(stored), int(row["hi"]) if row is not None else 1)
            cached = (start, start)
        seq, hi = cached
        if seq >= hi:
            hi = seq + SEQ_BLOCK
            with self.connection as con:
                con.execute(
                    "INSERT INTO conversation_seq (conversation_id, hi) VALUES (?, ?) "
                    "ON CONFLICT(conversation_id) DO UPDATE SET hi=excluded.hi",
                    (conv_id, hi),
                )
        self._event_seqs[conv_id] = (seq + 1, hi)
        return seq

    def append_event(
        self,
        site_id: str,
        user_id: int,
        conv_id: str,
        turn_id: str,
        event: dict[str, Any],
        *,
        seq: int | None = None,
        now: int | None = None,
    ) -> int:
        """Persist an event; returns its per-conversation sequence number.

        ``seq`` is one :meth:`next_seq` already handed out (a live event being stored);
        without it, the next one is allocated. A merged streamed run carries ``parts``,
        the lengths of its deltas, which went out live as the seqs ending at ``seq``
        (see :meth:`events`).
        """
        stamp = resolve_now(now)
        self._conversation_row(site_id, user_id, conv_id)
        if seq is None:
            seq = self.next_seq(conv_id)
        con = self.connection
        with con:
            payload = dict(event, seq=seq)
            con.execute(
                "INSERT INTO turn_events (conversation_id, seq, turn_id, sealed, created_at) "
                "VALUES (?,?,?,?,?)",
                (
                    conv_id,
                    seq,
                    turn_id,
                    self._seal_json(
                        self.chat_dek(site_id, user_id),
                        payload,
                        bound_to=aad("turn_events", conv_id, seq),
                    ),
                    stamp,
                ),
            )
            con.execute("UPDATE conversations SET updated_at=? WHERE id=?", (stamp, conv_id))
        return seq

    def events(
        self, site_id: str, user_id: int, conv_id: str, *, after_seq: int = 0
    ) -> list[tuple[int, dict[str, Any]]]:
        """Stored events after ``after_seq``, in order.

        A merged streamed run that a client saw only part of live (``after_seq`` falls
        inside it) comes back with just the deltas it has not seen yet.
        """
        self._conversation_row(site_id, user_id, conv_id)
        dek = self.chat_dek(site_id, user_id)
        rows = self.connection.execute(
            "SELECT seq, sealed FROM turn_events WHERE conversation_id=? AND seq>? ORDER BY seq",
            (conv_id, after_seq),
        ).fetchall()
        out: list[tuple[int, dict[str, Any]]] = []
        for r in rows:
            event = self._open_json(
                dek, r["sealed"], bound_to=aad("turn_events", conv_id, r["seq"])
            )
            unseen = _unseen_part(event, after_seq)
            if unseen is not None:
                out.append((r["seq"], unseen))
        return out

    def start_turn(
        self,
        site_id: str,
        user_id: int,
        conv_id: str,
        turn_id: str,
        *,
        client_msg_id: str | None = None,
        now: int | None = None,
    ) -> None:
        self._conversation_row(site_id, user_id, conv_id)
        with self.connection as con:
            con.execute(
                "INSERT INTO turns (turn_id, conversation_id, client_msg_id, state, created_at) "
                "VALUES (?,?,?,'running',?)",
                (turn_id, conv_id, client_msg_id, resolve_now(now)),
            )

    def turn_for_client_message(self, conv_id: str, client_msg_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT turn_id FROM turns WHERE conversation_id=? AND client_msg_id=?",
            (conv_id, client_msg_id),
        ).fetchone()
        return str(row[0]) if row else None

    def running_turn(self, conv_id: str) -> str | None:
        """The turn that blocks a new one: running, or waiting for the user's answer."""
        row = self.connection.execute(
            "SELECT turn_id FROM turns WHERE conversation_id=? AND state IN ('running','waiting') "
            "ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        return str(row[0]) if row else None

    def turn(self, conv_id: str, turn_id: str) -> Turn:
        row = self.connection.execute(
            "SELECT * FROM turns WHERE turn_id=? AND conversation_id=?", (turn_id, conv_id)
        ).fetchone()
        if row is None:
            raise StoreError("no such turn")
        return Turn(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            state=row["state"],
            created_at=row["created_at"],
            finished_at=row["finished_at"],
        )

    def set_turn_state(self, turn_id: str, state: str, *, now: int | None = None) -> None:
        finished = (
            resolve_now(now) if state in ("done", "failed", "cancelled", "interrupted") else None
        )
        with self.connection as con:
            con.execute(
                "UPDATE turns SET state=?, finished_at=? WHERE turn_id=?",
                (state, finished, turn_id),
            )

    def interrupt_running_turns(self) -> list[tuple[str, int, str, str]]:
        """Crash recovery: turns that were running when the process died."""
        rows = self.connection.execute(
            "SELECT t.turn_id, c.id, c.site_id, c.user_id FROM turns t "
            "JOIN conversations c ON c.id = t.conversation_id WHERE t.state='running'"
        ).fetchall()
        for r in rows:
            self.set_turn_state(r["turn_id"], "interrupted")
        return [(r["site_id"], r["user_id"], r["id"], r["turn_id"]) for r in rows]
