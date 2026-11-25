"""The text of the OpenAPI document: the introduction and the tag descriptions.

OpenAPI cannot describe a websocket, so the introduction carries the chat protocol. It is
taken from ``chat_ws``'s module docstring, which stays the single place that protocol is
written down.
"""

from __future__ import annotations

from typing import Any

from moodle_llm_chat.web.routes import chat_ws

INTRODUCTION = """\
The service behind the `local_llmchat` Moodle plugin. The plugin pushes course content to it,
and the plugin's chat page talks to it from the browser. Everything is read from `/openapi.json`;
this page is generated from the code.

## Two kinds of caller

| Caller | Credential | Used for |
|---|---|---|
| The Moodle plugin | **siteSecret**: a per-site bearer secret | `/ingest/...` |
| The browser | **chatToken**: a short-lived JWT, issued by the plugin | `/api/...`, `/ws/chat` |

Both go in the `Authorization: Bearer ...` header (the websocket takes the token in its first
frame). Plain HTTP is refused unless the operator turned TLS enforcement off for development.

## Syncing content

1. `POST /ingest/sync/start` opens a run (`full` or `incremental`).
2. For each course, `POST /ingest/manifest` sends hashes only. The answer names the documents
   the server needs.
3. `POST /ingest/documents` sends just those documents. The answer names the files still
   missing, which go to `PUT /ingest/blobs/{sha256}`.
4. `POST /ingest/sync/finish` closes a full run and removes what Moodle no longer has.

Every call is idempotent, so the plugin can retry after a lost response.

## The chat websocket

`/ws/chat` is not part of the OpenAPI paths. Its protocol:

```
"""

CLOSING = """
```
"""


def api_description() -> str:
    return INTRODUCTION + (chat_ws.__doc__ or "").strip("\n") + CLOSING


OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "ingest",
        "description": "Content the Moodle plugin pushes: sync runs, courses, documents and "
        "files. Authenticated with the site secret.",
    },
    {
        "name": "site",
        "description": "Site settings the plugin pushes and reads back: quota policy and usage "
        "report. Authenticated with the site secret.",
    },
    {
        "name": "conversations",
        "description": "The signed-in user's chat history. Authenticated with a chat token; "
        "a user only ever sees their own conversations.",
    },
    {
        "name": "quota",
        "description": "The signed-in user's token budget. Authenticated with a chat token.",
    },
    {"name": "health", "description": "Liveness check, no credentials."},
]
