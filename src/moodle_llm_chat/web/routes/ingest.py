"""Ingest routes: the Moodle plugin pushes courses, documents and blobs.

Every route is idempotent so the plugin's outbox can retry after a lost response:
documents upsert by content hash, blobs by SHA-256, deletes of unknown ids succeed,
and finishing an already-finished sync run is a no-op.

All routes are ``async def``: the store's connection belongs to the event loop thread.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Path, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from moodle_llm_chat.index.models import CourseIn, DocumentIn, SyncResult
from moodle_llm_chat.index.store import StoreError, SyncStateError
from moodle_llm_chat.web.auth import SITE_RESPONSES, SiteDep, StoreDep

router = APIRouter(prefix="/ingest", tags=["ingest"], responses=SITE_RESPONSES)

SHA256_PATH = Path(
    pattern=r"^[0-9a-f]{64}$",
    description="Lower-case hex SHA-256 of the file's bytes, a document's `file.sha256`.",
)
MAX_DOCUMENTS_PER_REQUEST = 500

CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"description": "The sync run does not exist, is already finished, or is not open."}
}


class SyncStart(BaseModel):
    sync_id: str = Field(
        min_length=1, max_length=64, description="Id the plugin chose for this sync run."
    )
    mode: Literal["full", "incremental"] = Field(
        description="`full` covers every course and ends by removing what is gone from Moodle; "
        "`incremental` only records the changes it is sent."
    )


class SyncRef(BaseModel):
    sync_id: str = Field(
        min_length=1,
        max_length=64,
        description="Id of a sync run opened with `/ingest/sync/start`.",
    )


class SyncStarted(BaseModel):
    sync_id: str = Field(description="The id of the sync run that is now open.")


class ManifestEntry(BaseModel):
    doc_id: str = Field(description="Document id, `{course_id}:{cmid}:{part}`.")
    content_hash: str = Field(description="Hash the plugin computed over the document's content.")
    timemodified: int | None = Field(
        default=None, description="Moodle's last-modified time of the module, Unix seconds."
    )
    #: Sent by plugins that hash placement; older ones omit it.
    placement_hash: str | None = Field(
        default=None,
        description="Hash of where the document sits (section, names, header path). "
        "Older plugins omit it.",
    )


class Manifest(BaseModel):
    """``entries`` as the plugin sends it (a list), or as a ``doc_id → hash`` map."""

    sync_id: str = Field(
        min_length=1, max_length=64, description="Id of the open sync run the manifest belongs to."
    )
    entries: list[ManifestEntry] | dict[str, str] = Field(
        description="The hashes of the documents the plugin currently has, as a list of "
        "entries or as a map from document id to content hash."
    )
    chunk_no: int | None = Field(
        default=None, description="Position of this part when a large manifest is split."
    )
    of: int | None = Field(default=None, description="Total number of parts of the manifest.")

    def hashes(self) -> dict[str, str | tuple[str, str | None]]:
        if isinstance(self.entries, dict):
            return dict(self.entries)
        return {e.doc_id: (e.content_hash, e.placement_hash) for e in self.entries}


class NeededDocuments(BaseModel):
    need: list[str] = Field(
        description="Ids of the documents whose content the plugin must now send."
    )


class CoursesBody(BaseModel):
    sync_id: str | None = Field(
        default=None, description="The open sync run these courses belong to, if any."
    )
    courses: list[CourseIn] = Field(description="The courses to create or update.")


class CourseIds(BaseModel):
    course_ids: list[int] = Field(description="Moodle ids of the courses that no longer exist.")


class DocumentsBody(BaseModel):
    sync_id: str | None = Field(
        default=None, description="The open sync run these documents belong to, if any."
    )
    documents: list[DocumentIn] = Field(
        description=f"The documents to create or update, at most {MAX_DOCUMENTS_PER_REQUEST}."
    )


class DocIds(BaseModel):
    doc_ids: list[str] = Field(description="Ids of the documents that no longer exist.")


class Count(BaseModel):
    count: int = Field(description="How many items the request contained.")


class DocumentsRemoved(BaseModel):
    documents_removed: int = Field(
        description="How many documents were removed together with their courses."
    )


class DocumentsChanged(BaseModel):
    changed: list[str] = Field(
        description="Ids of the documents that were new or whose content changed."
    )
    needs_blob: list[str] = Field(
        description="SHA-256 hashes of files the server does not have yet. "
        "Upload each with `PUT /ingest/blobs/{sha256}`."
    )


class BlobStored(BaseModel):
    sha256: str = Field(description="The hash of the file the request named.")
    stored: bool = Field(
        description="`true` when this request stored the bytes, `false` when the server "
        "already had them."
    )


def _conflict(exc: SyncStateError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/sync/start",
    summary="Open a sync run",
    responses=CONFLICT,
)
async def sync_start(body: SyncStart, site: SiteDep, store: StoreDep) -> SyncStarted:
    """Open a sync run before a full or incremental push.

    A full run must be finished with `/ingest/sync/finish`; only then are documents and
    courses that were not seen in any of its manifests removed. Opening a second full run
    while one is open is a conflict.
    """
    try:
        store.documents.start_sync(site.site_id, body.sync_id, mode=body.mode)
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    return SyncStarted(sync_id=body.sync_id)


@router.post(
    "/manifest",
    summary="Send the hashes of a course's documents",
    responses=CONFLICT,
)
async def manifest(body: Manifest, site: SiteDep, store: StoreDep) -> NeededDocuments:
    """Tell the server which documents exist, by hash, without sending their content.

    Documents with an unchanged hash are marked as seen in this run. The answer lists the
    documents whose content the server lacks or has stale, so the plugin sends only those.
    """
    try:
        need = store.documents.apply_manifest(site.site_id, body.sync_id, body.hashes())
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    return NeededDocuments(need=need)


@router.post(
    "/sync/finish",
    summary="Finish a sync run",
    responses=CONFLICT,
)
async def sync_finish(body: SyncRef, site: SiteDep, store: StoreDep) -> SyncResult:
    """Close a sync run.

    After a full run, every document and course that appeared in none of its manifests is
    removed (tombstoned) and the files nothing refers to any more are deleted. Finishing a
    run that is already finished does nothing.
    """
    try:
        result = store.documents.finish_sync(site.site_id, body.sync_id)
    except SyncStateError as exc:
        raise _conflict(exc) from exc
    store.blobs.delete_orphan_blobs()
    return result


# Bodies are accepted in the plugin's shape (a bare JSON array) or wrapped in an object.


@router.post("/courses", summary="Create or update courses")
async def courses(body: list[CourseIn] | CoursesBody, site: SiteDep, store: StoreDep) -> Count:
    """Upsert course records. The body is a JSON array of courses, or an object with a
    `courses` array and the `sync_id` of an open run."""
    items, sync_id = (body, None) if isinstance(body, list) else (body.courses, body.sync_id)
    store.documents.upsert_courses(site.site_id, items, sync_id=sync_id)
    return Count(count=len(items))


@router.post("/courses/delete", summary="Remove courses")
async def delete_courses(body: CourseIds, site: SiteDep, store: StoreDep) -> DocumentsRemoved:
    """Remove courses that no longer exist in Moodle, and with them all their documents.

    Unknown course ids are ignored, so the call can be repeated.
    """
    gone = store.documents.tombstone_courses(site.site_id, body.course_ids)
    return DocumentsRemoved(documents_removed=len(gone))


@router.post(
    "/documents",
    summary="Create or update documents",
    responses={
        413: {"description": f"More than {MAX_DOCUMENTS_PER_REQUEST} documents in one request."}
    },
)
async def documents(
    body: list[DocumentIn] | DocumentsBody, request: Request, site: SiteDep, store: StoreDep
) -> DocumentsChanged:
    """Upsert documents by content hash; an unchanged document is not processed again.

    The body is a JSON array of documents, or an object with a `documents` array and the
    `sync_id` of an open run. Changed documents are queued for text extraction; work done
    during a full run runs at bulk priority, everything else is urgent. The answer names
    the files the server still needs as blobs.
    """
    items, sync_id = (body, None) if isinstance(body, list) else (body.documents, body.sync_id)
    if len(items) > MAX_DOCUMENTS_PER_REQUEST:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"at most {MAX_DOCUMENTS_PER_REQUEST} documents per request",
        )
    result = store.documents.upsert_documents_detailed(site.site_id, items, sync_id=sync_id)
    needs_blob = store.blobs.missing_blobs(d.file.sha256 for d in items if d.file)
    if result.changed:
        # Pushes during an open full sync are bulk work; everything else is urgent.
        bulk = sync_id is not None or store.documents.has_open_full_sync(site.site_id)
        for listener in request.app.state.document_listeners:
            listener(site.site_id, result.changed, bulk)
    if result.relocated:
        for relocated in request.app.state.relocation_listeners:
            relocated(site.site_id, result.relocated)
    return DocumentsChanged(changed=result.changed, needs_blob=needs_blob)


@router.post("/documents/delete", summary="Remove documents")
async def delete_documents(body: list[str] | DocIds, site: SiteDep, store: StoreDep) -> Count:
    """Remove documents that no longer exist in Moodle.

    The body is a JSON array of document ids, or an object with a `doc_ids` array. Unknown
    ids are ignored, so the call can be repeated.
    """
    doc_ids = body if isinstance(body, list) else body.doc_ids
    store.documents.tombstone_documents(site.site_id, doc_ids)
    return Count(count=len(doc_ids))


@router.put(
    "/blobs/{sha256}",
    response_model=None,
    summary="Upload a file by its hash",
    responses={
        200: {"model": BlobStored, "description": "The server already had this file."},
        201: {"model": BlobStored, "description": "The file was stored."},
        404: {"description": "No document of this site refers to this hash."},
        413: {"description": "The file is larger than the configured maximum file size."},
        422: {"description": "The uploaded bytes do not hash to the SHA-256 in the path."},
    },
)
async def put_blob(
    request: Request,
    site: SiteDep,
    store: StoreDep,
    sha256: str = SHA256_PATH,
) -> JSONResponse:
    """Upload a file's bytes as the raw request body.

    Only hashes that a pushed document refers to are accepted. The bytes are verified
    against the hash, encrypted and stored once, however many documents use them. Sending
    a file the server already has is harmless and stores nothing.
    """
    if not store.blobs.blob_referenced(site.site_id, sha256):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no document references this blob")
    if store.blobs.blob_exists(sha256):
        return JSONResponse({"sha256": sha256, "stored": False}, status_code=200)
    limit = request.app.state.settings.pipeline.max_file_bytes
    writer = store.blobs.blob_writer(sha256, max_bytes=limit)
    try:
        async for chunk in request.stream():
            writer.write(chunk)
        writer.commit()
    except StoreError as exc:
        message = str(exc)
        code = (
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
            if "too large" in message
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(code, detail=message) from exc
    except BaseException:
        writer.abort()
        raise
    for listener in request.app.state.blob_listeners:
        listener(site.site_id, sha256)
    return JSONResponse({"sha256": sha256, "stored": True}, status_code=201)


@router.get("/status", summary="Show what the server holds for this site")
async def ingest_status(request: Request, site: SiteDep, store: StoreDep) -> dict[str, Any]:
    """Counts of courses and documents, the latest sync run and the state of the processing
    pipeline. The plugin's "Test connection" button calls this, so a 200 also proves the
    site secret is valid."""
    result = store.status(site.site_id)
    result["site_id"] = site.site_id
    pipeline = getattr(request.app.state, "pipeline_status", None)
    if pipeline is not None:
        result["pipeline"] = pipeline()
    return result
