"""Courses, documents and the sync runs that keep them in step with Moodle."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog

from moodle_llm_chat.index.models import (
    Course,
    CourseIn,
    Document,
    DocumentIn,
    DocumentKind,
    SyncResult,
)
from moodle_llm_chat.index.store.base import Repository
from moodle_llm_chat.index.store.errors import StoreError, SyncStateError
from moodle_llm_chat.index.store.helpers import placeholders, resolve_now

log = structlog.get_logger(__name__)

#: A sync run with no activity for this long can no longer tombstone anything.
SYNC_STALE_AFTER_S = 24 * 3600

#: Placement fields kept in a document's sealed payload (the rest are plain columns).
_SEALED_PLACEMENT = ("section_name", "module_name", "title", "header_path", "module_url")


@dataclass(frozen=True)
class UpsertResult:
    #: Content changed: extract (or fetch) again.
    changed: list[str] = field(default_factory=list)
    #: Only the header path changed: re-chunk from the stored segments.
    relocated: list[str] = field(default_factory=list)


class DocumentRepo(Repository):
    """Courses, documents and the sync runs that keep them in step with Moodle."""

    def upsert_courses(
        self,
        site_id: str,
        courses: Iterable[CourseIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> None:
        stamp = resolve_now(now)
        with self.connection as con:
            for c in courses:
                payload = self._seal_course_json(
                    site_id,
                    c.course_id,
                    {"fullname": c.fullname, "shortname": c.shortname},
                    "courses",
                    site_id,
                    c.course_id,
                )
                con.execute(
                    "INSERT INTO courses (site_id, course_id, payload, visible, startdate, "
                    "enddate, timemodified, sync_id, last_seen, seen_seq, tombstoned_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,NULL) "
                    "ON CONFLICT(site_id, course_id) DO UPDATE SET payload=excluded.payload, "
                    "visible=excluded.visible, startdate=excluded.startdate, "
                    "enddate=excluded.enddate, timemodified=excluded.timemodified, "
                    "sync_id=COALESCE(excluded.sync_id, courses.sync_id), "
                    "last_seen=excluded.last_seen, seen_seq=excluded.seen_seq, tombstoned_at=NULL",
                    (
                        site_id,
                        c.course_id,
                        payload,
                        int(c.visible),
                        c.startdate,
                        c.enddate,
                        c.timemodified,
                        sync_id,
                        stamp,
                        self._seq(),
                    ),
                )
        if sync_id:
            self._touch_sync(site_id, sync_id, stamp)

    def courses(self, site_id: str, *, course_ids: Collection[int] | None = None) -> list[Course]:
        sql = "SELECT * FROM courses WHERE site_id=? AND tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        if course_ids is not None:
            if not course_ids:
                return []
            sql += f" AND course_id IN ({placeholders(course_ids)})"
            params += list(course_ids)
        rows = self.connection.execute(sql + " ORDER BY course_id", params).fetchall()
        out = []
        for r in rows:
            p = self._open_course_json(
                site_id, r["course_id"], r["payload"], "courses", site_id, r["course_id"]
            )
            out.append(
                Course(
                    site_id=site_id,
                    course_id=r["course_id"],
                    fullname=p["fullname"],
                    shortname=p["shortname"],
                    visible=bool(r["visible"]),
                    startdate=r["startdate"],
                    enddate=r["enddate"],
                    timemodified=r["timemodified"],
                )
            )
        return out

    def tombstone_courses(
        self, site_id: str, course_ids: Collection[int], *, now: int | None = None
    ) -> list[str]:
        if not course_ids:
            return []
        stamp = resolve_now(now)
        doc_ids = [
            r[0]
            for r in self.connection.execute(
                f"SELECT doc_id FROM documents WHERE site_id=? AND tombstoned_at IS NULL "
                f"AND course_id IN ({placeholders(course_ids)})",
                [site_id, *course_ids],
            )
        ]
        self.tombstone_documents(site_id, doc_ids, now=stamp)
        with self.connection as con:
            con.execute(
                f"UPDATE courses SET tombstoned_at=? WHERE site_id=? "
                f"AND course_id IN ({placeholders(course_ids)})",
                [stamp, site_id, *course_ids],
            )
        return doc_ids

    def upsert_documents(
        self,
        site_id: str,
        docs: Iterable[DocumentIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> list[str]:
        """Upsert documents; return the ids whose content changed (need extraction)."""
        return self.upsert_documents_detailed(site_id, docs, sync_id=sync_id, now=now).changed

    def upsert_documents_detailed(
        self,
        site_id: str,
        docs: Iterable[DocumentIn],
        *,
        sync_id: str | None = None,
        now: int | None = None,
    ) -> UpsertResult:
        """Upsert documents; report which need extraction and which only re-chunking.

        A document whose content is unchanged keeps its extraction, but always takes
        its new placement: inserting a section shifts every later one, and a stale
        section order files documents under the wrong section.
        """
        stamp = resolve_now(now)
        result = UpsertResult()
        con = self.connection
        with con:
            for d in docs:
                existing = con.execute(
                    "SELECT content_hash, tombstoned_at, section_id, section_order, payload, "
                    "placement_hash FROM documents WHERE site_id=? AND doc_id=?",
                    (site_id, d.doc_id),
                ).fetchone()
                payload_fields = self._document_payload(d)
                if (
                    existing is not None
                    and existing["content_hash"] == d.content_hash
                    and existing["tombstoned_at"] is None
                ):
                    stored = self._open_course_json(
                        site_id, d.course_id, existing["payload"], "documents", site_id, d.doc_id
                    )
                    moved = (existing["section_id"], existing["section_order"]) != (
                        d.section_id,
                        d.section_order,
                    ) or any(stored.get(k) != payload_fields[k] for k in _SEALED_PLACEMENT)
                    if moved:
                        con.execute(
                            "UPDATE documents SET section_id=?, section_order=?, payload=? "
                            "WHERE site_id=? AND doc_id=?",
                            (
                                d.section_id,
                                d.section_order,
                                self._seal_document(site_id, d, payload_fields),
                                site_id,
                                d.doc_id,
                            ),
                        )
                        if stored.get("header_path") != payload_fields["header_path"]:
                            result.relocated.append(d.doc_id)
                    con.execute(
                        "UPDATE documents SET last_seen=?, seen_seq=?, "
                        "sync_id=COALESCE(?, sync_id), "
                        "placement_hash=COALESCE(?, placement_hash) WHERE site_id=? AND doc_id=?",
                        (stamp, self._seq(), sync_id, d.placement_hash, site_id, d.doc_id),
                    )
                    continue
                con.execute(
                    "INSERT INTO documents (site_id, doc_id, course_id, section_id, "
                    "section_order, cmid, modname, kind, payload, mimetype, filesize, "
                    "blob_sha256, timemodified, content_hash, extract_version, extracted_at, "
                    "skip_reason, first_seen, last_seen, sync_id, seen_seq, tombstoned_at, "
                    "placement_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,?,?,?,?,NULL,?) "
                    "ON CONFLICT(site_id, doc_id) DO UPDATE SET course_id=excluded.course_id, "
                    "section_id=excluded.section_id, section_order=excluded.section_order, "
                    "cmid=excluded.cmid, modname=excluded.modname, kind=excluded.kind, "
                    "payload=excluded.payload, mimetype=excluded.mimetype, "
                    "filesize=excluded.filesize, blob_sha256=excluded.blob_sha256, "
                    "timemodified=excluded.timemodified, content_hash=excluded.content_hash, "
                    "extract_version=NULL, skip_reason=NULL, last_seen=excluded.last_seen, "
                    "seen_seq=excluded.seen_seq, "
                    "sync_id=COALESCE(excluded.sync_id, documents.sync_id), tombstoned_at=NULL, "
                    "placement_hash=excluded.placement_hash",
                    (
                        site_id,
                        d.doc_id,
                        d.course_id,
                        d.section_id,
                        d.section_order,
                        d.cmid,
                        d.modname,
                        d.kind.value,
                        self._seal_document(site_id, d, payload_fields),
                        d.file.mimetype if d.file else None,
                        d.file.filesize if d.file else len((d.text or "").encode("utf-8")),
                        d.file.sha256 if d.file else None,
                        d.timemodified,
                        d.content_hash,
                        stamp,
                        stamp,
                        sync_id,
                        self._seq(),
                        d.placement_hash,
                    ),
                )
                result.changed.append(d.doc_id)
        if sync_id:
            self._touch_sync(site_id, sync_id, stamp)
        return result

    @staticmethod
    def _document_payload(d: DocumentIn) -> dict[str, Any]:
        return {
            "title": d.title,
            "section_name": d.section_name,
            "module_name": d.module_name,
            "header_path": d.header_path,
            "module_url": d.module_url,
            "external_url": d.external_url,
            "text": d.text,
            "filename": d.file.filename if d.file else None,
        }

    def _seal_document(self, site_id: str, d: DocumentIn, payload: dict[str, Any]) -> bytes:
        return self._seal_course_json(site_id, d.course_id, payload, "documents", site_id, d.doc_id)

    def document(self, site_id: str, doc_id: str) -> Document:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE site_id=? AND doc_id=?", (site_id, doc_id)
        ).fetchone()
        if row is None:
            raise StoreError(f"no such document: {doc_id!r}")
        return self._to_document(row)

    def live_documents(
        self,
        site_id: str,
        *,
        course_ids: Collection[int] | None = None,
        cmids: Collection[int] | None = None,
    ) -> list[Document]:
        sql = "SELECT * FROM documents WHERE site_id=? AND tombstoned_at IS NULL"
        params: list[Any] = [site_id]
        for column, values in (("course_id", course_ids), ("cmid", cmids)):
            if values is not None:
                if not values:
                    return []
                sql += f" AND {column} IN ({placeholders(values)})"
                params += list(values)
        sql += " ORDER BY course_id, section_order, doc_id"
        return [self._to_document(r) for r in self.connection.execute(sql, params)]

    def documents_needing_extraction(
        self, *, extract_version: int, limit: int | None = None
    ) -> list[Document]:
        sql = (
            "SELECT * FROM documents WHERE tombstoned_at IS NULL "
            "AND (extract_version IS NULL OR extract_version < ?) ORDER BY last_seen, doc_id"
        )
        params: list[Any] = [extract_version]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._to_document(r) for r in self.connection.execute(sql, params)]

    def record_extraction(
        self,
        site_id: str,
        doc_id: str,
        *,
        extract_version: int,
        skip_reason: str | None = None,
        now: int | None = None,
    ) -> None:
        with self.connection as con:
            con.execute(
                "UPDATE documents SET extract_version=?, extracted_at=?, skip_reason=? "
                "WHERE site_id=? AND doc_id=?",
                (extract_version, resolve_now(now), skip_reason, site_id, doc_id),
            )

    def tombstone_documents(
        self, site_id: str, doc_ids: Collection[str], *, now: int | None = None
    ) -> None:
        """Tombstone and drop content, so it stops being answerable immediately."""
        if not doc_ids:
            return
        stamp = resolve_now(now)
        with self.connection as con:
            for doc_id in doc_ids:
                self._db.chunks.delete_content(con, site_id, doc_id)
            con.execute(
                f"UPDATE documents SET tombstoned_at=? WHERE site_id=? "
                f"AND doc_id IN ({placeholders(doc_ids)})",
                [stamp, site_id, *doc_ids],
            )
        log.info("store.tombstoned", site=site_id, count=len(doc_ids))

    def _to_document(self, row: Any) -> Document:
        site_id = row["site_id"]
        p = self._open_course_json(
            site_id, row["course_id"], row["payload"], "documents", site_id, row["doc_id"]
        )
        return Document(
            site_id=site_id,
            doc_id=row["doc_id"],
            course_id=row["course_id"],
            section_id=row["section_id"],
            section_name=p["section_name"],
            section_order=row["section_order"],
            cmid=row["cmid"],
            modname=row["modname"],
            module_name=p["module_name"],
            title=p["title"],
            kind=DocumentKind(row["kind"]),
            header_path=p["header_path"],
            module_url=p["module_url"],
            external_url=p["external_url"],
            text=p["text"],
            filename=p["filename"],
            mimetype=row["mimetype"],
            filesize=row["filesize"],
            blob_sha256=row["blob_sha256"],
            timemodified=row["timemodified"],
            content_hash=row["content_hash"],
            extract_version=row["extract_version"],
            extracted_at=row["extracted_at"],
            skip_reason=row["skip_reason"],
            char_count=row["char_count"],
            page_count=row["page_count"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            tombstoned_at=row["tombstoned_at"],
        )

    def start_sync(self, site_id: str, sync_id: str, *, mode: str, now: int | None = None) -> None:
        if mode not in ("full", "incremental"):
            raise SyncStateError(f"unknown sync mode {mode!r}")
        stamp = resolve_now(now)
        with self.connection as con:
            con.execute(
                "INSERT INTO sync_runs (site_id, sync_id, mode, state, started_at, "
                "last_activity_at, start_seq) VALUES (?,?,?,'open',?,?,?) "
                "ON CONFLICT(site_id, sync_id) DO UPDATE SET "
                "last_activity_at=excluded.last_activity_at",
                (site_id, sync_id, mode, stamp, stamp, self._seq()),
            )

    def apply_manifest(
        self,
        site_id: str,
        sync_id: str,
        entries: Mapping[str, str | tuple[str, str | None]],
        *,
        now: int | None = None,
    ) -> list[str]:
        """Mark every listed document as seen in this run; return the ids to send.

        An entry is a content hash, or ``(content_hash, placement_hash)``. A document
        is needed when its content changed or, if the plugin sends one, its placement
        hash differs from the stored one (a moved or renamed section).

        Listed-but-changed documents count as seen too: if the plugin then fails to
        push one, the old version is kept rather than deleted.
        """
        stamp = resolve_now(now)
        self._open_run(site_id, sync_id, stamp)
        need: list[str] = []
        con = self.connection
        with con:
            for doc_id, entry in entries.items():
                content_hash, placement_hash = entry if isinstance(entry, tuple) else (entry, None)
                row = con.execute(
                    "SELECT content_hash, tombstoned_at, placement_hash FROM documents "
                    "WHERE site_id=? AND doc_id=?",
                    (site_id, doc_id),
                ).fetchone()
                if (
                    row is None
                    or row["tombstoned_at"] is not None
                    or row["content_hash"] != content_hash
                    or (placement_hash is not None and row["placement_hash"] != placement_hash)
                ):
                    need.append(doc_id)
                if row is not None and row["tombstoned_at"] is None:
                    con.execute(
                        "UPDATE documents SET sync_id=?, last_seen=?, seen_seq=? "
                        "WHERE site_id=? AND doc_id=?",
                        (sync_id, stamp, self._seq(), site_id, doc_id),
                    )
        self._touch_sync(site_id, sync_id, stamp)
        return sorted(need)

    def finish_sync(self, site_id: str, sync_id: str, *, now: int | None = None) -> SyncResult:
        stamp = resolve_now(now)
        run = self.connection.execute(
            "SELECT * FROM sync_runs WHERE site_id=? AND sync_id=?", (site_id, sync_id)
        ).fetchone()
        if run is None:
            raise SyncStateError(f"unknown sync run {sync_id!r}")
        if run["state"] == "finished":
            # A retried finish after a lost response: already done, nothing more to do.
            return SyncResult(
                sync_id=sync_id, mode=run["mode"], tombstoned=[], courses_tombstoned=[]
            )
        if run["state"] == "stale" or stamp - run["last_activity_at"] > SYNC_STALE_AFTER_S:
            with self.connection as con:
                con.execute(
                    "UPDATE sync_runs SET state='stale' WHERE site_id=? AND sync_id=?",
                    (site_id, sync_id),
                )
            raise SyncStateError(f"sync run {sync_id!r} is stale; start a new full sync")

        tombstoned: list[str] = []
        courses_gone: list[int] = []
        if run["mode"] == "full":
            # "Seen in this run" = listed in its manifest or pushed since it started.
            # The plugin does not tag pushes with the sync id, so activity order decides.
            started = run["start_seq"]
            tombstoned = [
                r[0]
                for r in self.connection.execute(
                    "SELECT doc_id FROM documents WHERE site_id=? AND tombstoned_at IS NULL "
                    "AND seen_seq < ? AND (sync_id IS NULL OR sync_id != ?) ORDER BY doc_id",
                    (site_id, started, sync_id),
                )
            ]
            self.tombstone_documents(site_id, tombstoned, now=stamp)
            courses_gone = [
                r[0]
                for r in self.connection.execute(
                    "SELECT course_id FROM courses WHERE site_id=? AND tombstoned_at IS NULL "
                    "AND seen_seq < ? AND (sync_id IS NULL OR sync_id != ?)",
                    (site_id, started, sync_id),
                )
            ]
            self.tombstone_courses(site_id, courses_gone, now=stamp)
        with self.connection as con:
            con.execute(
                "UPDATE sync_runs SET state='finished', finished_at=? "
                "WHERE site_id=? AND sync_id=?",
                (stamp, site_id, sync_id),
            )
        return SyncResult(
            sync_id=sync_id,
            mode=run["mode"],
            tombstoned=tombstoned,
            courses_tombstoned=courses_gone,
        )

    def has_open_full_sync(self, site_id: str, *, now: int | None = None) -> bool:
        cutoff = resolve_now(now) - SYNC_STALE_AFTER_S
        return (
            self.connection.execute(
                "SELECT 1 FROM sync_runs WHERE site_id=? AND mode='full' AND state='open' "
                "AND last_activity_at >= ? LIMIT 1",
                (site_id, cutoff),
            ).fetchone()
            is not None
        )

    def _open_run(self, site_id: str, sync_id: str, stamp: int) -> None:
        run = self.connection.execute(
            "SELECT state FROM sync_runs WHERE site_id=? AND sync_id=?", (site_id, sync_id)
        ).fetchone()
        if run is None or run["state"] != "open":
            raise SyncStateError(f"sync run {sync_id!r} is not open")

    def _touch_sync(self, site_id: str, sync_id: str, stamp: int) -> None:
        with self.connection as con:
            con.execute(
                "UPDATE sync_runs SET last_activity_at=? WHERE site_id=? AND sync_id=? "
                "AND state='open'",
                (stamp, site_id, sync_id),
            )

    def reset_extraction(self, *, only_skipped: bool = False) -> int:
        """Mark documents for re-processing; returns how many changed."""
        sql = (
            "UPDATE documents SET extract_version=NULL, skip_reason=NULL "
            "WHERE tombstoned_at IS NULL AND extract_version IS NOT NULL"
        )
        if only_skipped:
            sql += " AND skip_reason IS NOT NULL"
        with self.connection as con:
            n = con.execute(sql).rowcount
        return int(n or 0)
