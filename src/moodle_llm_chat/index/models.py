"""Data shapes: what the Moodle plugin pushes (``*In``) and what the Store returns."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

_SHA256_HEX = r"^[0-9a-f]{64}$"


class DocumentKind(StrEnum):
    #: Text sent inline by the plugin (module intro, label).
    INLINE = "inline"
    #: HTML sent inline (page content, book chapter).
    HTML = "html"
    #: A file whose bytes are uploaded separately as a blob.
    FILE = "file"
    #: An external URL the API fetches itself (YouTube, HackMD, TaskCards, …).
    EXTERNAL = "external"


class FileRef(BaseModel):
    sha256: str = Field(
        pattern=_SHA256_HEX,
        description="Lower-case hex SHA-256 of the file; upload it to `/ingest/blobs/{sha256}`.",
    )
    filename: str = Field(description="The file's name in Moodle.")
    mimetype: str | None = Field(default=None, description="MIME type as Moodle reports it.")
    filesize: int = Field(default=0, ge=0, description="Size in bytes.")


class CourseIn(BaseModel):
    course_id: int = Field(description="Moodle course id.")
    fullname: str = Field(description="Full course name.")
    shortname: str = Field(description="Short course name.")
    visible: bool = Field(default=True, description="Whether the course is visible to students.")
    startdate: int = Field(default=0, description="Course start, Unix seconds; 0 if not set.")
    enddate: int = Field(default=0, description="Course end, Unix seconds; 0 if not set.")
    timemodified: int = Field(default=0, description="Last change in Moodle, Unix seconds.")


class DocumentIn(BaseModel):
    """One answerable unit of course content, as the plugin builds it.

    ``doc_id`` is ``"{course_id}:{cmid}:{part}"``. ``content_hash`` is computed by the
    plugin over the content (inline text, file hash, external URL), so an unchanged
    document is skipped without re-extraction. ``placement_hash`` covers where it sits
    (section, names, header path): when only that changes, the store updates the
    placement and keeps the extracted content.
    """

    doc_id: str = Field(
        min_length=1, max_length=255, description="Document id, `{course_id}:{cmid}:{part}`."
    )
    course_id: int = Field(description="Moodle id of the course the document belongs to.")
    section_id: int = Field(default=0, description="Moodle id of the course section.")
    section_name: str = Field(default="", description="Name of the course section.")
    section_order: int = Field(default=0, description="Position of the section in the course.")
    cmid: int = Field(description="Moodle course-module id; what the access check is made on.")
    modname: str = Field(default="", description="Moodle module type, e.g. `resource` or `page`.")
    module_name: str = Field(default="", description="Name of the module in the course.")
    title: str = Field(default="", description="Title shown when the document is cited.")
    kind: DocumentKind = Field(
        description="How the content arrives: `inline` text, `html`, an uploaded `file`, or an "
        "`external` URL the server fetches itself."
    )
    header_path: list[str] = Field(
        default_factory=list, description="Headings above the content, outermost first."
    )
    module_url: str | None = Field(default=None, description="Link to the module in Moodle.")
    timemodified: int = Field(default=0, description="Last change in Moodle, Unix seconds.")
    text: str | None = Field(
        default=None, description="The content itself; required for `inline` and `html`."
    )
    file: FileRef | None = Field(default=None, description="The file; required for `file`.")
    external_url: str | None = Field(
        default=None, description="The URL to fetch; required for `external`."
    )
    content_hash: str = Field(
        min_length=1,
        max_length=128,
        description="Hash the plugin computed over the content; unchanged means not re-processed.",
    )
    #: Older plugins send none; placement is then compared field by field.
    placement_hash: str | None = Field(
        default=None,
        max_length=128,
        description="Hash of where the document sits; when only this changes the extracted "
        "content is kept. Older plugins omit it.",
    )

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> DocumentIn:
        if self.kind is DocumentKind.FILE and self.file is None:
            raise ValueError("a 'file' document needs a file reference")
        if self.kind in (DocumentKind.INLINE, DocumentKind.HTML) and self.text is None:
            raise ValueError(f"an {self.kind.value!r} document needs text")
        if self.kind is DocumentKind.EXTERNAL and not self.external_url:
            raise ValueError("an 'external' document needs external_url")
        return self


class SegmentIn(BaseModel):
    """A unit of extracted text — a PDF page, a slide, a sheet, or a whole document."""

    ordinal: int
    text: str
    page: int | None = None
    label: str | None = None


class ChunkIn(BaseModel):
    ordinal: int
    text: str
    header_text: str = ""
    page: int | None = None


class Site(BaseModel):
    site_id: str
    name: str
    url: str
    created_at: int
    revoked_at: int | None = None


class Course(BaseModel):
    site_id: str
    course_id: int
    fullname: str
    shortname: str
    visible: bool
    startdate: int
    enddate: int
    timemodified: int


class Document(BaseModel):
    site_id: str
    doc_id: str
    course_id: int
    section_id: int
    section_name: str
    section_order: int
    cmid: int
    modname: str
    module_name: str
    title: str
    kind: DocumentKind
    header_path: list[str]
    module_url: str | None
    external_url: str | None
    text: str | None
    filename: str | None
    mimetype: str | None
    filesize: int
    blob_sha256: str | None
    timemodified: int
    content_hash: str
    extract_version: int | None
    extracted_at: int | None
    skip_reason: str | None
    char_count: int
    page_count: int
    first_seen: int
    last_seen: int
    tombstoned_at: int | None

    @property
    def needs_extraction(self) -> bool:
        return self.extract_version is None

    @property
    def header_text(self) -> str:
        return " › ".join(self.header_path)


class Segment(BaseModel):
    doc_id: str
    ordinal: int
    text: str
    page: int | None
    label: str | None


class Chunk(BaseModel):
    chunk_id: int
    site_id: str
    doc_id: str
    ordinal: int
    text: str
    header_text: str
    page: int | None


class SyncResult(BaseModel):
    sync_id: str = Field(description="The sync run that was finished.")
    mode: str = Field(description="`full` or `incremental`.")
    tombstoned: list[str] = Field(
        description="Ids of the documents removed because no manifest of the run listed them."
    )
    courses_tombstoned: list[int] = Field(
        description="Ids of the courses removed because the run did not list them."
    )


class Job(BaseModel):
    id: int
    site_id: str
    doc_id: str
    stage: str
    state: str
    priority: int
    attempts: int
    next_attempt_at: int
    last_error: str | None


class Conversation(BaseModel):
    id: str
    site_id: str
    user_id: int
    course_id: int | None
    title: str
    created_at: int
    updated_at: int


class Turn(BaseModel):
    turn_id: str
    conversation_id: str
    state: str
    created_at: int
    finished_at: int | None
