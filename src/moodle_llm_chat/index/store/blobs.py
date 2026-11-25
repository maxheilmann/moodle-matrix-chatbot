"""Encrypted uploaded files on disk, keyed by their sha256."""

from __future__ import annotations

import hashlib
import io
import secrets
import time
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import structlog

from moodle_llm_chat.crypto.envelope import StreamEncryptor, aad, decrypt_stream
from moodle_llm_chat.crypto.keys import Scope
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError

log = structlog.get_logger(__name__)


class _HashingLimitedWriter:
    """Hashes and counts plaintext on its way into the encryptor."""

    def __init__(self, enc: StreamEncryptor, max_bytes: int | None) -> None:
        self._enc = enc
        self._max = max_bytes
        self.sha = hashlib.sha256()
        self.size = 0

    def write(self, data: bytes) -> None:
        self.size += len(data)
        if self._max is not None and self.size > self._max:
            raise StoreError(f"blob too large (limit {self._max} bytes)")
        self.sha.update(data)
        self._enc.write(data)


class BlobWriter:
    """Streams an upload into an encrypted temp file; :meth:`commit` verifies and keeps it.

    Plaintext never touches disk: bytes are encrypted as they arrive.
    """

    def __init__(self, repo: BlobRepo, expected_sha256: str, max_bytes: int | None) -> None:
        self._repo = repo
        self._sha = expected_sha256
        self._final = repo.blob_path(expected_sha256)
        self._final.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self._final.with_name(f"{self._final.name}.{secrets.token_hex(4)}.tmp")
        self._fh: BinaryIO = self._tmp.open("wb")
        self._key_existed = repo._db.wrapped_key(Scope.BLOB.value, expected_sha256) is not None
        dek = repo.ring.dek(repo._db, Scope.BLOB, expected_sha256)
        self._enc = StreamEncryptor(self._fh, dek, aad=aad("blob", expected_sha256))
        self._writer = _HashingLimitedWriter(self._enc, max_bytes)
        self._done = False

    def write(self, data: bytes) -> None:
        try:
            self._writer.write(data)
        except Exception:
            self.abort()
            raise

    def commit(self) -> None:
        try:
            self._enc.close()
            self._fh.close()
            if self._writer.sha.hexdigest() != self._sha:
                raise StoreError("blob digest does not match its sha256")
            self._tmp.replace(self._final)
        except Exception:
            self.abort()
            raise
        with self._repo.connection as con:
            con.execute(
                "INSERT OR IGNORE INTO blobs (sha256, size, created_at) VALUES (?,?,?)",
                (self._sha, self._writer.size, int(time.time())),
            )
        self._done = True

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        if not self._fh.closed:
            self._fh.close()
        self._tmp.unlink(missing_ok=True)
        if not self._key_existed and not self._repo.blob_exists(self._sha):
            self._repo.ring.forget(self._repo._db, Scope.BLOB, self._sha)


class BlobRepo(Repository):
    """Encrypted uploaded files on disk, keyed by their sha256."""

    def blob_path(self, sha256: str) -> Path:
        return self._db.blobs_dir / sha256[:2] / f"{sha256}.enc"

    def blob_exists(self, sha256: str) -> bool:
        return (
            self.connection.execute("SELECT 1 FROM blobs WHERE sha256=?", (sha256,)).fetchone()
            is not None
            and self.blob_path(sha256).exists()
        )

    def documents_with_blob(self, site_id: str, sha256: str) -> list[str]:
        return [
            r[0]
            for r in self.connection.execute(
                "SELECT doc_id FROM documents WHERE site_id=? AND blob_sha256=? "
                "AND tombstoned_at IS NULL ORDER BY doc_id",
                (site_id, sha256),
            )
        ]

    def blob_referenced(self, site_id: str, sha256: str) -> bool:
        """Whether a live document of ``site_id`` points at this blob."""
        return (
            self.connection.execute(
                "SELECT 1 FROM documents WHERE site_id=? AND blob_sha256=? "
                "AND tombstoned_at IS NULL LIMIT 1",
                (site_id, sha256),
            ).fetchone()
            is not None
        )

    def missing_blobs(self, shas: Iterable[str]) -> list[str]:
        return [s for s in dict.fromkeys(shas) if not self.blob_exists(s)]

    def blob_writer(self, expected_sha256: str, *, max_bytes: int | None = None) -> BlobWriter:
        return BlobWriter(self, expected_sha256, max_bytes)

    def put_blob(
        self, source: BinaryIO, *, expected_sha256: str, max_bytes: int | None = None
    ) -> None:
        if self.blob_exists(expected_sha256):
            return
        writer = self.blob_writer(expected_sha256, max_bytes=max_bytes)
        while chunk := source.read(1024 * 1024):
            writer.write(chunk)
        writer.commit()

    def delete_orphan_blobs(self) -> list[str]:
        """Delete blobs no live document references; their keys are shredded too."""
        orphans = [
            r[0]
            for r in self.connection.execute(
                "SELECT sha256 FROM blobs WHERE sha256 NOT IN (SELECT blob_sha256 FROM documents "
                "WHERE blob_sha256 IS NOT NULL AND tombstoned_at IS NULL) ORDER BY sha256"
            )
        ]
        for sha in orphans:
            self.blob_path(sha).unlink(missing_ok=True)
            with self.connection as con:
                con.execute("DELETE FROM blobs WHERE sha256=?", (sha,))
            self.ring.forget(self._db, Scope.BLOB, sha)
        if orphans:
            log.info("store.orphan_blobs_deleted", count=len(orphans))
        return orphans

    def read_blob(self, sha256: str) -> bytes:
        if not self.blob_exists(sha256):
            raise StoreError(f"no such blob: {sha256}")
        dek = self.ring.dek(self._db, Scope.BLOB, sha256)
        out = io.BytesIO()
        with self.blob_path(sha256).open("rb") as fh:
            decrypt_stream(fh, out, dek, aad=aad("blob", sha256))
        return out.getvalue()
