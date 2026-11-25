"""Extracted content: segments, chunks, search queries, vectors and the embedding/OCR caches."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Collection, Sequence
from typing import Any

from moodle_llm_chat.crypto.envelope import aad, open_box, seal
from moodle_llm_chat.crypto.keys import Scope
from moodle_llm_chat.index.models import Chunk, ChunkIn, Segment, SegmentIn
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError
from moodle_llm_chat.index.store.helpers import pack_vector, placeholders, unpack_vector

#: The chunks that still wait for an embedding (the FROM and WHERE of the queries about them).
_WITHOUT_VECTOR = (
    "FROM chunks c LEFT JOIN chunk_vectors v ON v.chunk_id = c.chunk_id WHERE v.chunk_id IS NULL"
)


class ChunkRepo(Repository):
    """Extracted content: segments, chunks, search queries, vectors and the embedding/OCR caches."""

    def replace_content(
        self,
        site_id: str,
        doc_id: str,
        segments: Sequence[SegmentIn],
        chunks: Sequence[ChunkIn],
    ) -> list[int]:
        """Atomically swap a document's segments and chunks; returns new chunk ids."""
        row = self.connection.execute(
            "SELECT course_id FROM documents WHERE site_id=? AND doc_id=?", (site_id, doc_id)
        ).fetchone()
        if row is None:
            raise StoreError(f"no such document: {doc_id!r}")
        course_id = row["course_id"]
        dek = self._course_dek(site_id, course_id)
        ids: list[int] = []
        con = self.connection
        with con:
            self.delete_content(con, site_id, doc_id)
            for s in segments:
                con.execute(
                    "INSERT INTO segments (site_id, doc_id, ordinal, course_id, page, chars, "
                    "sealed) VALUES (?,?,?,?,?,?,?)",
                    (
                        site_id,
                        doc_id,
                        s.ordinal,
                        course_id,
                        s.page,
                        len(s.text),
                        self._seal_json(
                            dek,
                            {"text": s.text, "label": s.label},
                            bound_to=aad("segments", site_id, doc_id, s.ordinal),
                        ),
                    ),
                )
            for c in chunks:
                cur = con.execute(
                    "INSERT INTO chunks (site_id, doc_id, ordinal, course_id, page, text_sha256, "
                    "sealed) VALUES (?,?,?,?,?,?,?)",
                    (
                        site_id,
                        doc_id,
                        c.ordinal,
                        course_id,
                        c.page,
                        hashlib.sha256(c.text.encode("utf-8")).hexdigest(),
                        self._seal_json(
                            dek,
                            {"text": c.text, "header_text": c.header_text},
                            bound_to=aad("chunks", site_id, doc_id, c.ordinal),
                        ),
                    ),
                )
                chunk_id = int(cur.lastrowid)
                ids.append(chunk_id)
                con.execute(
                    "INSERT INTO chunks_fts (rowid, text, header_text) VALUES (?,?,?)",
                    (chunk_id, c.text, c.header_text),
                )
            pages = {s.page for s in segments if s.page is not None}
            con.execute(
                "UPDATE documents SET char_count=?, page_count=? WHERE site_id=? AND doc_id=?",
                (sum(len(s.text) for s in segments), len(pages), site_id, doc_id),
            )
        return ids

    @staticmethod
    def delete_content(con: Any, site_id: str, doc_id: str) -> None:
        ids = [
            r[0]
            for r in con.execute(
                "SELECT chunk_id FROM chunks WHERE site_id=? AND doc_id=?", (site_id, doc_id)
            )
        ]
        if ids:
            con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders(ids)})", ids)
            con.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({placeholders(ids)})", ids)
        con.execute("DELETE FROM chunks WHERE site_id=? AND doc_id=?", (site_id, doc_id))
        con.execute("DELETE FROM segments WHERE site_id=? AND doc_id=?", (site_id, doc_id))

    def segments(self, site_id: str, doc_id: str) -> list[Segment]:
        rows = self.connection.execute(
            "SELECT * FROM segments WHERE site_id=? AND doc_id=? ORDER BY ordinal",
            (site_id, doc_id),
        ).fetchall()
        out = []
        for r in rows:
            p = self._open_course_json(
                site_id, r["course_id"], r["sealed"], "segments", site_id, doc_id, r["ordinal"]
            )
            out.append(
                Segment(
                    doc_id=doc_id,
                    ordinal=r["ordinal"],
                    text=p["text"],
                    page=r["page"],
                    label=p["label"],
                )
            )
        return out

    def chunks(self, site_id: str, chunk_ids: Sequence[int]) -> list[Chunk]:
        """Decrypted chunks of ``site_id``, in the order of ``chunk_ids``."""
        if not chunk_ids:
            return []
        rows = {
            r["chunk_id"]: r
            for r in self.connection.execute(
                f"SELECT * FROM chunks WHERE site_id=? AND chunk_id IN ({placeholders(chunk_ids)})",
                [site_id, *chunk_ids],
            )
        }
        return [self._to_chunk(rows[i]) for i in chunk_ids if i in rows]

    def chunks_for_document(self, site_id: str, doc_id: str) -> list[Chunk]:
        rows = self.connection.execute(
            "SELECT * FROM chunks WHERE site_id=? AND doc_id=? ORDER BY ordinal", (site_id, doc_id)
        ).fetchall()
        return [self._to_chunk(r) for r in rows]

    def chunk_count(self, site_id: str) -> int:
        return int(
            self.connection.execute(
                "SELECT count(*) FROM chunks WHERE site_id=?", (site_id,)
            ).fetchone()[0]
        )

    def _to_chunk(self, row: Any) -> Chunk:
        site_id = row["site_id"]
        p = self._open_course_json(
            site_id,
            row["course_id"],
            row["sealed"],
            "chunks",
            site_id,
            row["doc_id"],
            row["ordinal"],
        )
        return Chunk(
            chunk_id=row["chunk_id"],
            site_id=site_id,
            doc_id=row["doc_id"],
            ordinal=row["ordinal"],
            text=p["text"],
            header_text=p["header_text"],
            page=row["page"],
        )

    def _scope_sql(
        self,
        site_id: str,
        *,
        cmids: Collection[int] | None,
        since: int | None,
        until: int | None,
        doc_ids: Collection[str] | None = None,
    ) -> tuple[str, list[Any]]:
        """WHERE fragment (on aliases ``c``/``d``) restricting chunks to a scope."""
        sql = " c.site_id=? AND d.tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        if cmids is not None:
            sql += f" AND d.cmid IN ({placeholders(cmids) or 'NULL'})"
            params += list(cmids)
        if doc_ids is not None:
            sql += f" AND d.doc_id IN ({placeholders(doc_ids) or 'NULL'})"
            params += list(doc_ids)
        if since is not None:
            sql += " AND d.timemodified >= ?"
            params.append(since)
        if until is not None:
            sql += " AND d.timemodified <= ?"
            params.append(until)
        return sql, params

    def fts_chunk_ids(
        self,
        site_id: str,
        match: str,
        *,
        limit: int,
        cmids: Collection[int] | None = None,
        doc_ids: Collection[str] | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> list[int]:
        """BM25-ranked chunk ids for an FTS5 MATCH expression, within a scope."""
        where, params = self._scope_sql(
            site_id, cmids=cmids, since=since, until=until, doc_ids=doc_ids
        )
        rows = self.connection.execute(
            "SELECT c.chunk_id FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.rowid "
            "JOIN documents d ON d.site_id = c.site_id AND d.doc_id = c.doc_id "
            f"WHERE chunks_fts MATCH ? AND{where} ORDER BY f.rank LIMIT ?",
            [match, *params, limit],
        ).fetchall()
        return [r[0] for r in rows]

    def vector_chunk_ids(
        self,
        site_id: str,
        query_vector: Sequence[float],
        *,
        limit: int,
        cmids: Collection[int] | None = None,
        doc_ids: Collection[str] | None = None,
        since: int | None = None,
        until: int | None = None,
    ) -> list[int]:
        """Exact cosine nearest neighbours among exactly the chunks in scope."""
        where, params = self._scope_sql(
            site_id, cmids=cmids, since=since, until=until, doc_ids=doc_ids
        )
        rows = self.connection.execute(
            "SELECT c.chunk_id, vec_distance_cosine(v.embedding, ?) AS dist "
            "FROM chunk_vectors v JOIN chunks c ON c.chunk_id = v.chunk_id "
            "JOIN documents d ON d.site_id = c.site_id AND d.doc_id = c.doc_id "
            f"WHERE{where} ORDER BY dist LIMIT ?",
            [pack_vector(query_vector), *params, limit],
        ).fetchall()
        return [r[0] for r in rows]

    def chunks_without_vectors(
        self,
        *,
        limit: int,
        exclude_ids: Collection[int] = (),
        exclude_sites: Collection[str] = (),
    ) -> list[Chunk]:
        sql = f"SELECT c.* {_WITHOUT_VECTOR}"
        params: list[Any] = []
        if exclude_sites:
            sql += f" AND c.site_id NOT IN ({placeholders(exclude_sites)})"
            params += list(exclude_sites)
        if exclude_ids:
            sql += f" AND c.chunk_id NOT IN ({placeholders(exclude_ids)})"
            params += list(exclude_ids)
        rows = self.connection.execute(
            sql + " ORDER BY c.chunk_id LIMIT ?", [*params, limit]
        ).fetchall()
        return [self._to_chunk(r) for r in rows]

    def sites_with_pending_vectors(self) -> list[str]:
        return [
            r[0] for r in self.connection.execute(f"SELECT DISTINCT c.site_id {_WITHOUT_VECTOR}")
        ]

    def pending_vector_count(self) -> int:
        return int(self.connection.execute(f"SELECT count(*) {_WITHOUT_VECTOR}").fetchone()[0])

    def set_embedding(self, chunk_id: int, vector: Sequence[float]) -> None:
        if len(vector) != self._db.embed_dim:
            raise StoreError(f"vector has {len(vector)} dimensions, expected {self._db.embed_dim}")
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO chunk_vectors (chunk_id, embedding) VALUES (?,?)",
                (chunk_id, pack_vector(vector)),
            )

    def clear_embeddings(self) -> int:
        with self.connection as con:
            n = con.execute("DELETE FROM chunk_vectors").rowcount
            con.execute("DELETE FROM embedding_cache")
        return int(n or 0)

    def cached_embedding(self, text_sha256: str, key: str) -> list[float] | None:
        row = self.connection.execute(
            "SELECT vector FROM embedding_cache WHERE lookup=? AND key=?",
            (self._lookup(text_sha256), key),
        ).fetchone()
        return unpack_vector(bytes(row[0])) if row else None

    def cache_embedding(self, text_sha256: str, key: str, vector: list[float]) -> None:
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO embedding_cache (lookup, key, vector, created_at) "
                "VALUES (?,?,?,?)",
                (self._lookup(text_sha256), key, pack_vector(vector), int(time.time())),
            )

    def cached_ocr(self, image_sha256: str, model_tag: str) -> str | None:
        lookup = self._lookup(image_sha256)
        row = self.connection.execute(
            "SELECT sealed FROM ocr_cache WHERE lookup=? AND model_tag=?", (lookup, model_tag)
        ).fetchone()
        if row is None:
            return None
        dek = self.ring.dek(self._db, Scope.CONTENT, "ocr-cache")
        return open_box(dek, bytes(row[0]), aad=aad("ocr", lookup, model_tag))

    def cache_ocr(self, image_sha256: str, model_tag: str, text: str) -> None:
        lookup = self._lookup(image_sha256)
        dek = self.ring.dek(self._db, Scope.CONTENT, "ocr-cache")
        with self.connection as con:
            con.execute(
                "INSERT OR REPLACE INTO ocr_cache (lookup, model_tag, sealed, created_at) "
                "VALUES (?,?,?,?)",
                (
                    lookup,
                    model_tag,
                    seal(dek, text, aad=aad("ocr", lookup, model_tag)),
                    int(time.time()),
                ),
            )
