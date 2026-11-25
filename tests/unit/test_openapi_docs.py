"""The OpenAPI document (``/openapi.json``, shown at ``/docs`` and ``/redoc``) documents the API.

A route added without a description, an auth scheme, its error responses or described
fields fails here, so the generated reference cannot silently fall behind the code.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from moodle_llm_chat.config import Settings
from moodle_llm_chat.crypto.keys import KeyRing
from moodle_llm_chat.web.app import create_app

#: Schemas FastAPI adds itself for 422 responses.
GENERATED_SCHEMAS = {"HTTPValidationError", "ValidationError"}
#: Operations that need no credentials.
PUBLIC = {("get", "/healthz")}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("MLC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MLC_EMBEDDING__DIM", "3")
    app = create_app(Settings(), ring=KeyRing(os.urandom(32)), start_workers=False)
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def spec(client: TestClient) -> dict[str, Any]:
    body: dict[str, Any] = client.get("/openapi.json").json()
    return body


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in {"get", "put", "post", "patch", "delete"}
    ]


def test_the_reference_pages_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_every_operation_has_a_description(spec: dict[str, Any]) -> None:
    missing = [
        f"{m.upper()} {p}"
        for m, p, op in operations(spec)
        if len(op.get("description", "").strip()) < 30
    ]
    assert not missing, f"operations without a real description: {missing}"


def test_every_operation_has_a_summary_that_is_not_the_function_name(
    spec: dict[str, Any],
) -> None:
    # FastAPI derives "Sync Start" from ``def sync_start``; a written summary reads
    # like a sentence and is not that title-cased name.
    generic = [
        f"{m.upper()} {p}"
        for m, p, op in operations(spec)
        if not op.get("summary", "").strip() or op["summary"] == op["summary"].title()
    ]
    assert not generic, f"operations with a generated summary: {generic}"


def test_every_operation_belongs_to_a_described_tag(spec: dict[str, Any]) -> None:
    described = {t["name"] for t in spec.get("tags", []) if t.get("description")}
    used = {tag for _, _, op in operations(spec) for tag in op.get("tags", [])}
    assert used, "operations are not tagged"
    assert used <= described, f"tags without a description: {sorted(used - described)}"
    assert all(op.get("tags") for _, _, op in operations(spec)), "an operation has no tag"


def test_credentials_and_auth_failures_are_declared(spec: dict[str, Any]) -> None:
    for method, path, op in operations(spec):
        if (method, path) in PUBLIC:
            continue
        assert op.get("security"), f"{method.upper()} {path} does not declare its credentials"
        assert "401" in op["responses"], f"{method.upper()} {path} does not document 401"
        assert "403" in op["responses"], f"{method.upper()} {path} does not document 403"


def test_the_two_credential_kinds_are_described(spec: dict[str, Any]) -> None:
    schemes = spec["components"]["securitySchemes"]
    assert set(schemes) == {"siteSecret", "chatToken"}
    for scheme in schemes.values():
        assert scheme["type"] == "http"
        assert scheme["scheme"] == "bearer"
        assert len(scheme["description"]) > 40


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("post", "/ingest/sync/start", "409"),
        ("post", "/ingest/manifest", "409"),
        ("post", "/ingest/sync/finish", "409"),
        ("post", "/ingest/documents", "413"),
        ("put", "/ingest/blobs/{sha256}", "404"),
        ("put", "/ingest/blobs/{sha256}", "413"),
        ("get", "/api/conversations/{conversation_id}", "404"),
        ("patch", "/api/conversations/{conversation_id}", "404"),
        ("delete", "/api/conversations/{conversation_id}", "404"),
    ],
)
def test_route_specific_errors_are_documented(
    spec: dict[str, Any], method: str, path: str, status: str
) -> None:
    responses = spec["paths"][path][method]["responses"]
    assert status in responses, f"{method.upper()} {path} does not document {status}"
    assert responses[status].get("description"), f"{status} has no description"


def test_every_parameter_is_described(spec: dict[str, Any]) -> None:
    missing = [
        f"{m.upper()} {p}: {param['name']}"
        for m, p, op in operations(spec)
        for param in op.get("parameters", [])
        if not param.get("description")
    ]
    assert not missing, f"parameters without a description: {missing}"


def test_every_schema_property_is_described(spec: dict[str, Any]) -> None:
    missing = [
        f"{name}.{prop}"
        for name, schema in spec["components"]["schemas"].items()
        if name not in GENERATED_SCHEMAS
        for prop, definition in schema.get("properties", {}).items()
        if not definition.get("description")
    ]
    assert not missing, f"fields without a description: {missing}"


def test_responses_have_schemas_that_say_what_comes_back(spec: dict[str, Any]) -> None:
    """Success bodies are typed models, not an anonymous ``object``."""
    anonymous = []
    for method, path, op in operations(spec):
        for status, response in op["responses"].items():
            if not status.startswith("2") or status == "204":
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema", {})
            if not schema or schema == {"type": "object"} or "additionalProperties" in schema:
                anonymous.append(f"{method.upper()} {path} {status}")
    # The status report and conversation history are open-ended by design.
    allowed = {
        "GET /ingest/status 200",
        "GET /api/conversations/{conversation_id} 200",
        "GET /healthz 200",
    }
    assert set(anonymous) <= allowed, (
        f"untyped success responses: {sorted(set(anonymous) - allowed)}"
    )


def test_the_websocket_protocol_is_part_of_the_api_description(spec: dict[str, Any]) -> None:
    """OpenAPI cannot describe websockets, so the introduction carries the protocol."""
    description = spec["info"]["description"]
    assert "/ws/chat" in description
    for frame in ("auth", "send", "answer", "resume", "continue", "cancel", "ping"):
        assert f"\n    {frame}" in description or f" {frame} " in description, frame
    for code in ("4400", "4401", "4403", "4429", "4503"):
        assert code in description, f"close code {code} is not documented"
    assert "siteSecret" in description or "site secret" in description.lower()
