"""Conversation history for the chat UI (chat-token bearer auth, owner only)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from moodle_llm_chat.access.tokens import TokenError, VerifiedToken, verify_chat_token
from moodle_llm_chat.index.store import StoreError
from moodle_llm_chat.web.auth import (
    CHAT_RESPONSES,
    UNAUTHORIZED,
    StoreDep,
    bearer_token,
    chat_scheme,
    require_tls,
)

router = APIRouter(prefix="/api/conversations", tags=["conversations"], responses=CHAT_RESPONSES)

NOT_FOUND_RESPONSE: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such conversation, or it belongs to another user."}
}


async def chat_user(
    request: Request,
    store: StoreDep,
    _tls: Annotated[None, Depends(require_tls)],
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(chat_scheme)] = None,
) -> VerifiedToken:
    token = bearer_token(request)
    if token is None:
        raise UNAUTHORIZED
    try:
        # Read-only listing does not burn the token; websocket frames do.
        return verify_chat_token(token, store, consume=False)
    except TokenError as exc:
        raise UNAUTHORIZED from exc


ChatUser = Annotated[VerifiedToken, Depends(chat_user)]

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")


ConversationId = Annotated[
    str, Path(description="Id of a conversation, as returned by the list or by `accepted`.")
]


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="The new title.")


class ConversationSummary(BaseModel):
    id: str = Field(description="Conversation id.")
    title: str = Field(description="Title; empty until one has been generated or set.")
    course_id: int | None = Field(description="Moodle course the chat was started in, if any.")
    created_at: int = Field(description="Creation time, Unix seconds.")
    updated_at: int = Field(description="Time of the last activity, Unix seconds.")


class Renamed(BaseModel):
    id: str = Field(description="Conversation id.")
    title: str = Field(description="The title now stored.")


@router.get("", summary="List the user's conversations")
async def list_conversations(v: ChatUser, store: StoreDep) -> list[ConversationSummary]:
    """The signed-in user's conversations on this site, most recently active first.

    At most 100 are returned and archived ones are left out. Only the owner can see a
    conversation.
    """
    return [
        ConversationSummary(
            id=c.id,
            title=c.title,
            course_id=c.course_id,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in store.conversations(v.site.site_id, v.scope.user_id)
    ]


@router.get(
    "/{conversation_id}",
    summary="Read a conversation's history",
    responses=NOT_FOUND_RESPONSE,
)
async def get_conversation(
    conversation_id: ConversationId,
    v: ChatUser,
    store: StoreDep,
    request: Request,
    after_seq: Annotated[
        int, Query(description="Return only events after this sequence number.")
    ] = 0,
) -> dict[str, Any]:
    """Stored history after ``after_seq``: one event per streamed run, not per delta.

    The answer has `conversation` (its metadata), `events` (the stored events in order),
    `last_seq` (the number to pass as `after_seq` next time) and `tools` (the UI metadata
    of the tools, the same as the websocket's `ready` frame, so a replayed history can
    label its tool cards).

    A run still being streamed is not stored yet; the websocket resumes it from
    ``last_seq``.
    """
    try:
        conversation = store.conversation(v.site.site_id, v.scope.user_id, conversation_id)
        rows = store.events(v.site.site_id, v.scope.user_id, conversation_id, after_seq=after_seq)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return {
        "conversation": conversation.model_dump(exclude={"site_id", "user_id"}),
        "events": [e for _, e in rows],
        "last_seq": rows[-1][0] if rows else after_seq,
        "tools": request.app.state.registry.manifest(),
    }


@router.patch(
    "/{conversation_id}",
    summary="Rename a conversation",
    responses=NOT_FOUND_RESPONSE,
)
async def rename_conversation(
    conversation_id: ConversationId, body: Rename, v: ChatUser, store: StoreDep
) -> Renamed:
    """Set the title of one of the user's conversations."""
    try:
        store.set_conversation_title(v.site.site_id, v.scope.user_id, conversation_id, body.title)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return Renamed(id=conversation_id, title=body.title)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a conversation",
    responses=NOT_FOUND_RESPONSE,
)
async def archive_conversation(
    conversation_id: ConversationId, v: ChatUser, store: StoreDep
) -> Response:
    """Archive a conversation: it leaves the list and can no longer be opened.

    The stored history is kept; it is not deleted.
    """
    try:
        store.archive_conversation(v.site.site_id, v.scope.user_id, conversation_id)
    except StoreError as exc:
        raise NOT_FOUND from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
