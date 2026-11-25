"""The event log of the conversations: stored events, the streamed run in flight, live clients.

Every event gets a per-conversation ``seq`` and is stored (sealed) before it is published,
except streamed ``token``/``thinking`` deltas: those are published one by one but buffered, and
each contiguous run is stored as *one* event when it ends (at the next event of another kind,
or when the turn ends however it ends). A client that drops and reconnects gets exactly what it
missed from :meth:`EventLog.history` (stored events plus the buffered run, a partly seen stored
run trimmed to its unseen part), then live events from its :meth:`EventLog.subscribe` queue.
"""

from __future__ import annotations

import asyncio
from typing import Any

from moodle_llm_chat.access.tokens import VerifiedToken
from moodle_llm_chat.agent.events import STREAMED
from moodle_llm_chat.index.store import Store

QUEUE_SIZE = 1000


class EventLog:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        #: The streamed run being published per conversation, not stored yet.
        self._runs: dict[str, list[dict[str, Any]]] = {}

    def subscribe(self, conversation_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.setdefault(conversation_id, set()).add(queue)
        return queue

    def unsubscribe(self, conversation_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(conversation_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                del self._subs[conversation_id]

    def history(
        self, v: VerifiedToken, conversation_id: str, *, after_seq: int
    ) -> list[dict[str, Any]]:
        """Every event after ``after_seq``; raises ``StoreError`` for a conversation not theirs."""
        stored = self.store.chat.events(
            v.site.site_id, v.scope.user_id, conversation_id, after_seq=after_seq
        )
        buffered = [e for e in self._runs.get(conversation_id, ()) if e["seq"] > after_seq]
        return [event for _, event in stored] + buffered

    def publish(self, v: VerifiedToken, conversation_id: str, event: dict[str, Any]) -> None:
        event = dict(event, seq=self.store.chat.next_seq(conversation_id))
        run = self._runs.get(conversation_id)
        if run and (
            event["type"] != run[-1]["type"] or event.get("turn_id") != run[-1].get("turn_id")
        ):
            self.flush(v, conversation_id)
        if event["type"] in STREAMED:
            self._runs.setdefault(conversation_id, []).append(event)
        else:
            self._store(v, conversation_id, event)
        for queue in list(self._subs.get(conversation_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stuck client: drop it; it will resume from its last seq on reconnect.
                self.unsubscribe(conversation_id, queue)

    def flush(self, v: VerifiedToken, conversation_id: str) -> None:
        """Store the buffered streamed run as one event carrying the last delta's seq.

        Its deltas went out with consecutive seqs (nothing else is published in between);
        ``parts`` records their lengths so a client that saw part of the run live can be
        sent just the rest (see :meth:`ChatRepo.events`).
        """
        run = self._runs.pop(conversation_id, None)
        if not run:
            return
        deltas = [e.get("delta") for e in run]
        merged = dict(
            run[-1],
            delta=None if all(d is None for d in deltas) else "".join(d or "" for d in deltas),
            parts=[len(d or "") for d in deltas],
        )
        self._store(v, conversation_id, merged)

    def _store(self, v: VerifiedToken, conversation_id: str, event: dict[str, Any]) -> None:
        self.store.chat.append_event(
            v.site.site_id,
            v.scope.user_id,
            conversation_id,
            event.get("turn_id", ""),
            event,
            seq=event["seq"],
        )
