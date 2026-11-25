"""The store itself: the database connection, the keys and the repositories built on them."""

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.index.db import open_encrypted
from moodle_llm_chat.index.store.blobs import BlobRepo
from moodle_llm_chat.index.store.chat import ChatRepo
from moodle_llm_chat.index.store.checkpoints import CheckpointRepo
from moodle_llm_chat.index.store.chunks import ChunkRepo
from moodle_llm_chat.index.store.documents import DocumentRepo
from moodle_llm_chat.index.store.errors import StoreError
from moodle_llm_chat.index.store.jobs import JobRepo
from moodle_llm_chat.index.store.schema import SCHEMA, SCHEMA_VERSION, migrate
from moodle_llm_chat.index.store.sites import SiteRepo
from moodle_llm_chat.index.store.usage import UsageRepo


class Store:
    """The encrypted database. Also the :class:`~moodle_llm_chat.crypto.keys.KeyStore`.

    It owns the connection, the key ring and the "seen" sequence, and offers one repository per
    area of the data: ``store.sites``, ``store.documents``, ``store.chunks``, ``store.blobs``,
    ``store.jobs``, ``store.chat``, ``store.usage`` and ``store.checkpoints``.
    """

    def __init__(
        self,
        path: Path | str,
        ring: KeyRing,
        *,
        embed_dim: int,
        blobs_dir: Path | None = None,
        reset_embeddings: bool = False,
    ) -> None:
        self.path = Path(path)
        self._reset_embeddings = reset_embeddings
        self.ring = ring
        self.embed_dim = embed_dim
        self.blobs_dir = blobs_dir or self.path.parent / "blobs"
        self._con: Any = None
        self._lookup_key = ring.derive("lookup")
        self._last_seq = 0
        self.sites = SiteRepo(self)
        self.documents = DocumentRepo(self)
        self.chunks = ChunkRepo(self)
        self.blobs = BlobRepo(self)
        self.jobs = JobRepo(self)
        self.chat = ChatRepo(self)
        self.usage = UsageRepo(self)
        self.checkpoints = CheckpointRepo(self)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        con = open_encrypted(self.path, self.ring)
        try:
            con.executescript(SCHEMA)
            meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
            version = int(meta.get("schema_version", SCHEMA_VERSION))
            if version > SCHEMA_VERSION:
                raise StoreError(
                    f"{self.path} has schema version {version}; this build supports "
                    f"{SCHEMA_VERSION}. Upgrade rather than risk corrupting the index."
                )
            migrate(con, version)
            stored_dim = meta.get("embed_dim")
            if self._reset_embeddings:
                con.execute("DELETE FROM chunk_vectors")
                con.execute("DELETE FROM embedding_cache")
            elif stored_dim is not None and int(stored_dim) != self.embed_dim:
                raise StoreError(
                    f"the index was built with embedding dimension {stored_dim}, but "
                    f"{self.embed_dim} is configured. Run `moodle-llm-chat reembed` after "
                    "changing the embedding model."
                )
            for table in ("documents", "courses"):
                row = con.execute(f"SELECT coalesce(max(seen_seq), 0) FROM {table}").fetchone()
                self._last_seq = max(self._last_seq, int(row[0]))
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?), ('embed_dim', ?)",
                (str(SCHEMA_VERSION), str(self.embed_dim)),
            )
            con.commit()
        except Exception:
            con.close()
            raise
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self._con = con

    def close(self) -> None:
        if self._con is not None:
            self._con.commit()
            self._con.close()
            self._con = None

    @property
    def connection(self) -> Any:
        if self._con is None:
            raise StoreError("store is not open")
        return self._con

    def seen_seq(self) -> int:
        """A strictly increasing "seen" sequence: orders activity within one second.

        Full syncs delete what was not seen since they started; wall-clock seconds
        cannot order a push and a sync start that happen in the same second.
        """
        self._last_seq = max(self._last_seq + 1, time.time_ns())
        return self._last_seq

    def lookup(self, value: str) -> str:
        return hmac.new(self._lookup_key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def wrapped_key(self, scope: str, scope_id: str) -> bytes | None:
        row = self.connection.execute(
            "SELECT wrapped FROM keys WHERE scope=? AND scope_id=?", (scope, scope_id)
        ).fetchone()
        return bytes(row[0]) if row else None

    def store_wrapped_key(self, scope: str, scope_id: str, wrapped: bytes) -> None:
        with self.connection as con:
            con.execute(
                "INSERT INTO keys (scope, scope_id, wrapped, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(scope, scope_id) DO UPDATE SET wrapped=excluded.wrapped",
                (scope, scope_id, wrapped, int(time.time())),
            )

    def delete_wrapped_key(self, scope: str, scope_id: str) -> None:
        with self.connection as con:
            con.execute("DELETE FROM keys WHERE scope=? AND scope_id=?", (scope, scope_id))

    def all_wrapped_keys(self) -> list[tuple[str, str, bytes]]:
        return [
            (r[0], r[1], bytes(r[2]))
            for r in self.connection.execute("SELECT scope, scope_id, wrapped FROM keys")
        ]

    def rotate_master_key(self, new_ring: KeyRing) -> int:
        """Re-wrap every data key under ``new_ring`` and re-key the database file.

        Payloads are not re-encrypted: their data keys do not change. Returns the
        number of re-wrapped keys. The old master key no longer opens the store.
        """
        count = new_ring.rewrap_all(self, previous=self.ring)
        self.connection.execute(f'PRAGMA rekey = "{new_ring.sqlcipher_pragma_value()}"')
        self.ring = new_ring
        self._lookup_key = new_ring.derive("lookup")
        self._rehash_lookups()
        return count

    def _rehash_lookups(self) -> None:
        """Keyed lookups derive from the master key, so they are recomputed on rotation."""
        con = self.connection
        with con:
            for row in con.execute("SELECT site_id FROM sites").fetchall():
                current = self.sites.site_secret(row[0])
                con.execute(
                    "UPDATE sites SET secret_lookup=?, prev_secret_lookup=NULL, "
                    "sealed_prev_secret=NULL, prev_valid_until=NULL WHERE site_id=?",
                    (self.lookup(current), row[0]),
                )
            # Caches are cheap to rebuild and keyed by the old lookup key.
            con.execute("DELETE FROM embedding_cache")
            con.execute("DELETE FROM ocr_cache")

    def status(self, site_id: str) -> dict[str, Any]:
        con = self.connection
        docs = con.execute(
            "SELECT count(*), sum(extract_version IS NULL), sum(skip_reason IS NOT NULL) "
            "FROM documents WHERE site_id=? AND tombstoned_at IS NULL",
            (site_id,),
        ).fetchone()
        last = con.execute(
            "SELECT sync_id, mode, state, started_at, finished_at FROM sync_runs "
            "WHERE site_id=? ORDER BY started_at DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        return {
            "courses": int(
                con.execute(
                    "SELECT count(*) FROM courses WHERE site_id=? AND tombstoned_at IS NULL",
                    (site_id,),
                ).fetchone()[0]
            ),
            "documents": int(docs[0] or 0),
            "pending_extraction": int(docs[1] or 0),
            "skipped": int(docs[2] or 0),
            "chunks": self.chunks.chunk_count(site_id),
            "last_sync": dict(last) if last else None,
        }
