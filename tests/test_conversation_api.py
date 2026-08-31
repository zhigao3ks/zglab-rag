"""Phase 15A2 authenticated Conversation API and ask persistence tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_auth_api import (
    ACTIVE_PASSWORD,
    ASK_URL,
    ORIGIN,
    STREAM_URL,
    ask_headers,
    authed_client,
    login,
    provision_active_user,
)
from tests.test_public_api import _make_insufficient_result
from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import MessageRole
from zglab_rag.conversation.repositories import MessageRepository

CONVERSATIONS_URL = "/api/v2/conversations"


def create_conversation(client: TestClient, csrf: str, title: str = "Project") -> dict:
    response = client.post(CONVERSATIONS_URL, json={"title": title}, headers=ask_headers(csrf))
    assert response.status_code == 201
    return response.json()


def persisted_messages(path: Path, owner_user_id: int, conversation_id: int) -> list:
    connection = ConversationDatabase(path).connect(initialize=True)
    try:
        return MessageRepository(connection).list_for_conversation(
            owner_user_id=owner_user_id, conversation_id=conversation_id
        )
    finally:
        connection.close()


def test_conversation_api_requires_authentication(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, _csrf = authed_client(tmp_path)
    anonymous = TestClient(client.app)  # type: ignore[attr-defined]

    assert anonymous.get(CONVERSATIONS_URL).status_code == 401
    assert anonymous.post(
        CONVERSATIONS_URL, json={"title": "Private"}, headers={"origin": ORIGIN}
    ).status_code == 401


def test_create_list_get_update_delete_and_messages(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, csrf = authed_client(tmp_path)
    created = create_conversation(client, csrf, "  Initial title  ")
    conversation_url = f"{CONVERSATIONS_URL}/{created['id']}"

    assert created["title"] == "Initial title"
    assert client.get(CONVERSATIONS_URL).json() == [created]
    assert client.get(conversation_url).json() == created
    assert client.get(f"{conversation_url}/messages").json() == []

    updated = client.patch(
        conversation_url, json={"title": " Renamed "}, headers=ask_headers(csrf)
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Renamed"

    deleted = client.delete(conversation_url, headers=ask_headers(csrf))
    assert deleted.status_code == 204
    assert client.get(conversation_url).status_code == 404


def test_conversation_owner_isolation_matches_nonexistent_response(tmp_path: Path) -> None:
    alice, app, settings, auth_runtime, _runtime, alice_csrf = authed_client(tmp_path)
    created = create_conversation(alice, alice_csrf)
    provision_active_user(auth_runtime, settings, username="bob")
    bob = TestClient(app)
    bob_login = login(bob, username="bob", password=ACTIVE_PASSWORD)
    bob_csrf = bob_login.json()["csrf_token"]
    owned_url = f"{CONVERSATIONS_URL}/{created['id']}"
    missing_url = f"{CONVERSATIONS_URL}/999999"

    for path in (owned_url, missing_url):
        response = bob.get(path)
        assert response.status_code == 404
        assert response.json()["error"] == {
            "code": "NOT_FOUND",
            "message": "Conversation not found",
        }
        assert bob.get(f"{path}/messages").status_code == 404
        patched = bob.patch(path, json={"title": "No"}, headers=ask_headers(bob_csrf))
        assert patched.status_code == 404
        assert bob.delete(path, headers=ask_headers(bob_csrf)).status_code == 404


def test_conversation_write_routes_require_csrf(tmp_path: Path) -> None:
    client, _app, _settings, _auth, _runtime, csrf = authed_client(tmp_path)
    assert client.post(
        CONVERSATIONS_URL, json={"title": "No CSRF"}, headers={"origin": ORIGIN}
    ).status_code == 403

    created = create_conversation(client, csrf)
    url = f"{CONVERSATIONS_URL}/{created['id']}"
    patched = client.patch(url, json={"title": "No CSRF"}, headers={"origin": ORIGIN})
    assert patched.status_code == 403
    assert client.delete(url, headers={"origin": ORIGIN}).status_code == 403


def test_ask_without_conversation_id_keeps_existing_behavior(tmp_path: Path) -> None:
    client, _app, settings, _auth, runtime, csrf = authed_client(tmp_path)
    response = client.post(ASK_URL, json={"question": "Independent"}, headers=ask_headers(csrf))

    assert response.status_code == 200
    assert runtime.service.last_question == "Independent"
    connection = ConversationDatabase(settings.conversation_database_path).connect(initialize=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    finally:
        connection.close()


def test_ask_persists_user_and_business_answer_without_loading_history(tmp_path: Path) -> None:
    client, _app, settings, _auth, runtime, csrf = authed_client(tmp_path)
    created = create_conversation(client, csrf)
    connection = ConversationDatabase(settings.conversation_database_path).connect(initialize=True)
    try:
        MessageRepository(connection).append(
            owner_user_id=1,
            conversation_id=created["id"],
            role=MessageRole.USER,
            content="Older persisted question",
        )
    finally:
        connection.close()

    response = client.post(
        ASK_URL,
        json={
            "question": "Only this question reaches the capability",
            "conversation_id": created["id"],
        },
        headers=ask_headers(csrf),
    )
    assert response.status_code == 200
    assert runtime.service.last_question == "Only this question reaches the capability"
    messages = persisted_messages(settings.conversation_database_path, 1, created["id"])
    assert [(message.role, message.content) for message in messages] == [
        (MessageRole.USER, "Older persisted question"),
        (MessageRole.USER, "Only this question reaches the capability"),
        (MessageRole.ASSISTANT, response.json()["answer"]),
    ]


def test_insufficient_and_sse_completed_results_are_persisted(tmp_path: Path) -> None:
    client, _app, settings, _auth, runtime, csrf = authed_client(tmp_path)
    insufficient = create_conversation(client, csrf, "Insufficient")
    runtime.service.result = _make_insufficient_result("No evidence")
    response = client.post(
        ASK_URL,
        json={"question": "No evidence", "conversation_id": insufficient["id"]},
        headers=ask_headers(csrf),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    insufficient_messages = persisted_messages(
        settings.conversation_database_path, 1, insufficient["id"]
    )
    assert [message.role for message in insufficient_messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]

    runtime.service.result = None
    streamed = create_conversation(client, csrf, "Stream")
    stream = client.post(
        STREAM_URL,
        json={"question": "Streamed", "conversation_id": streamed["id"]},
        headers=ask_headers(csrf),
    )
    assert stream.status_code == 200
    assert "event: completed" in stream.text
    streamed_messages = persisted_messages(settings.conversation_database_path, 1, streamed["id"])
    assert [message.role for message in streamed_messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]


def test_admission_failures_and_nonowner_binding_do_not_persist(tmp_path: Path) -> None:
    client, app, settings, auth_runtime, runtime, csrf = authed_client(
        tmp_path, auth_user_requests_per_minute=0
    )
    created = create_conversation(client, csrf)
    conversation_id = created["id"]
    assert client.post(
        ASK_URL,
        json={"question": "Quota denied", "conversation_id": conversation_id},
        headers=ask_headers(csrf),
    ).status_code == 429
    assert client.post(
        ASK_URL,
        json={"question": " ", "conversation_id": conversation_id},
        headers=ask_headers(csrf),
    ).status_code == 400
    assert client.post(
        ASK_URL,
        json={"question": "No CSRF", "conversation_id": conversation_id},
        headers={"origin": ORIGIN},
    ).status_code == 403
    assert persisted_messages(settings.conversation_database_path, 1, conversation_id) == []

    provision_active_user(auth_runtime, settings, username="bob")
    bob = TestClient(app)
    bob_csrf = login(bob, username="bob", password=ACTIVE_PASSWORD).json()["csrf_token"]
    denied = bob.post(
        ASK_URL,
        json={"question": "Not mine", "conversation_id": conversation_id},
        headers=ask_headers(bob_csrf),
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "NOT_FOUND"
    assert runtime.service.call_count == 0
    assert persisted_messages(settings.conversation_database_path, 1, conversation_id) == []
