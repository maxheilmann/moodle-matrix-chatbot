"""The embedding loop: one worker that gives every chunk without a vector its embedding.

Embedding is not a per-document job: the loop batches *all* chunks without a vector, across
documents, so a 300-page PDF and a one-line label share batches. Its persisted state is simply
"chunks without vectors". A chunk the provider keeps rejecting is set aside after three
attempts, and a provider that is down makes the loop back off instead of burning requests.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import structlog

from moodle_llm_chat.index.store import Store
from moodle_llm_chat.llm.embed import CachingEmbedder

log = structlog.get_logger(__name__)


class EmbeddingLoop:
    def __init__(
        self,
        store: Store,
        embedder: CachingEmbedder | None,
        *,
        batch: int,
        gate: Callable[[str], bool] | None,
        meter: Callable[[str, int], None] | None,
        idle_wait_s: float,
        max_backoff_s: float,
    ) -> None:
        self.store = store
        self.embedder = embedder
        #: Set to make the loop look for work now instead of at its next idle check.
        self.wake = asyncio.Event()
        #: Ingest quotas pause embedding: chunks stay pending instead of spending tokens.
        self.paused = False
        #: The last batch stored nothing: the provider is down or rejecting everything.
        self.failing = False
        self._batch = batch
        self._gate = gate
        self._meter = meter
        self._idle_wait_s = idle_wait_s
        self._max_backoff_s = max_backoff_s
        self._embed_backoff = 0.0
        self._poisoned: dict[int, int] = {}

    def pause(self, paused: bool) -> None:
        self.paused = paused
        if not paused:
            self.wake.set()

    def pending(self) -> int:
        return len(
            self.store.chunks.chunks_without_vectors(limit=1, exclude_ids=self._poisoned_ids())
        )

    def _poisoned_ids(self) -> set[int]:
        return {cid for cid, n in self._poisoned.items() if n >= 3}

    def _gated_sites(self) -> list[str]:
        if self._gate is None:
            return []
        return [s for s in self.store.chunks.sites_with_pending_vectors() if not self._gate(s)]

    async def run(self) -> None:
        assert self.embedder is not None
        wake = self.wake
        while True:
            batch = (
                []
                if self.paused
                else self.store.chunks.chunks_without_vectors(
                    limit=self._batch,
                    exclude_ids=self._poisoned_ids(),
                    exclude_sites=self._gated_sites(),
                )
            )
            if not batch:
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=self._idle_wait_s)
                continue
            try:
                vectors = await self.embedder.embed_documents(
                    [c.text for c in batch], skip_failures=True
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("pipeline.embed_error", error=type(exc).__name__)
                vectors = [None] * len(batch)
            stored = 0
            for chunk, vector in zip(batch, vectors, strict=True):
                if vector is None:
                    self._poisoned[chunk.chunk_id] = self._poisoned.get(chunk.chunk_id, 0) + 1
                    continue
                try:
                    self.store.chunks.set_embedding(chunk.chunk_id, vector)
                    stored += 1
                except Exception:  # chunk replaced while we were waiting on the provider
                    continue
                self._poisoned.pop(chunk.chunk_id, None)
                if self._meter is not None:
                    # Providers rarely report embedding usage: estimate ~4 chars/token.
                    self._meter(chunk.site_id, max(1, len(chunk.text) // 4))
            if stored == 0:
                # The provider is down or rejecting everything: back off, keep the work.
                self.failing = True
                self._embed_backoff = min(max(self._embed_backoff * 2, 1.0), self._max_backoff_s)
                self._poisoned.clear()
                wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), timeout=self._embed_backoff)
            else:
                self.failing = False
                self._embed_backoff = 0.0
