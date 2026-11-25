"""Plugin → API authentication: one per-site bearer secret over HTTPS.

Deliberately simple and conventional (the same model as webhook secrets): TLS gives
confidentiality, integrity and replay protection; a high-entropy secret, looked up
by keyed hash and compared in constant time, identifies the site.

Every dependency here is ``async def``: FastAPI runs plain ``def`` dependencies in a
threadpool, and the store's SQLite connection may only be used from the event loop
thread that opened it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from moodle_llm_chat.index.models import Site
from moodle_llm_chat.index.store import Store

UNAUTHORIZED = HTTPException(status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

# The two credential kinds, declared so the OpenAPI document (and Swagger UI's
# "Authorize" button) shows them. ``auto_error=False``: the checks below stay in charge of
# the answer, these only describe the header.
site_scheme = HTTPBearer(
    scheme_name="siteSecret",
    auto_error=False,
    description=(
        "The per-site secret printed by `moodle-llm-chat site add` (or `site rotate`) and "
        "pasted into the Moodle plugin's settings. Used by the plugin, server to server. "
        "During a rotation both the old and the new secret are accepted."
    ),
)
chat_scheme = HTTPBearer(
    scheme_name="chatToken",
    auto_error=False,
    bearerFormat="JWT",
    description=(
        "A chat token issued by the Moodle plugin for the signed-in user: HS256, at most "
        "15 minutes old, with the `kid` header set to the site id. Used by the browser. "
        "Read-only calls do not consume the token."
    ),
)

#: Error responses every route behind the site secret can give.
SITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {
        "description": "The Authorization header is missing, or the site secret is unknown "
        "or revoked."
    },
    403: {"description": "TLS is required and the request arrived over plain HTTP."},
}
#: Error responses every route behind a chat token can give.
CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "The chat token is missing, invalid, expired, or its site is revoked."},
    403: {"description": "TLS is required and the request arrived over plain HTTP."},
}


async def get_store(request: Request) -> Store:
    store: Store = request.app.state.store
    return store


async def require_tls(request: Request) -> None:
    """Refuse plain HTTP unless explicitly disabled (local development only).

    Behind a TLS-terminating proxy, run uvicorn with ``--proxy-headers`` so the
    forwarded scheme is seen here.
    """
    if request.app.state.settings.security.require_tls and request.url.scheme != "https":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="TLS required")


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def site_auth(
    request: Request,
    store: Annotated[Store, Depends(get_store)],
    _tls: Annotated[None, Depends(require_tls)],
    _credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(site_scheme)] = None,
) -> Site:
    token = bearer_token(request)
    if token is None:
        raise UNAUTHORIZED
    site = store.sites.authenticate_site(token)
    if site is None:
        raise UNAUTHORIZED
    store.sites.touch_site(site.site_id)
    return site


SiteDep = Annotated[Site, Depends(site_auth)]
StoreDep = Annotated[Store, Depends(get_store)]
