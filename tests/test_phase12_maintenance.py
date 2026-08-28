"""Post-seal Phase 12 maintenance regressions."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_auth_api import (
    ORIGIN,
    RESET_PASSWORD_URL,
    STREAM_URL,
    ask_headers,
    authed_client,
    build_app,
)


def test_reset_password_is_covered_by_request_body_limit(tmp_path: Path) -> None:
    app, _settings, _auth_runtime, _runtime = build_app(
        tmp_path, api_max_request_body_bytes=128
    )
    client = TestClient(app)
    oversized = b'{"token":"' + (b"x" * 200) + b'","password":"long-enough-password"}'

    response = client.post(
        RESET_PASSWORD_URL,
        content=oversized,
        headers={"origin": ORIGIN, "content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_v2_sse_completion_log_uses_actual_request_path(
    tmp_path: Path, caplog
) -> None:
    client, _app, _settings, _auth_runtime, _runtime, csrf = authed_client(tmp_path)

    with caplog.at_level(logging.INFO, logger="zglab_rag.api.main"):
        with client.stream(
            "POST",
            STREAM_URL,
            json={"question": "什么是 RAG？"},
            headers=ask_headers(csrf),
        ) as response:
            assert response.status_code == 200
            list(response.iter_text())

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "path=/api/v2/ask/stream" in message and "status=answered" in message
        for message in messages
    )
    assert not any(
        "path=/api/v1/ask/stream" in message and "status=answered" in message
        for message in messages
    )
