"""Phase 15B bounded context assembly and API lifecycle tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_auth_api import ASK_URL, STREAM_URL, ask_headers, authed_client
from tests.test_conversation_api import create_conversation
from zglab_rag.conversation.context import assemble_conversation_context
from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import Message, MessageRole
from zglab_rag.conversation.repositories import MessageRepository


def _message(identifier: int, role: MessageRole, content: str, conversation_id: int = 1) -> Message:
    return Message(
        id=identifier,
        conversation_id=conversation_id,
        role=role,
        content=content,
        created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=identifier),
    )


def test_context_assembly_keeps_complete_recent_turns_in_chronological_order() -> None:
    context = assemble_conversation_context(
        conversation_id=1,
        messages=[
            _message(1, MessageRole.USER, "old question"),
            _message(2, MessageRole.ASSISTANT, "old answer"),
            _message(3, MessageRole.USER, "recent question"),
            _message(4, MessageRole.ASSISTANT, "recent answer"),
            _message(5, MessageRole.USER, "dangling failure"),
        ],
        max_turns=2,
        max_chars=100,
        max_message_chars=30,
    )
    assert [message.content for message in context.messages] == [
        "old question",
        "old answer",
        "recent question",
        "recent answer",
    ]
    assert context.turn_count == 2
    assert context.truncated is False


def test_context_assembly_is_bounded_and_deterministic() -> None:
    messages = [
        _message(1, MessageRole.USER, "a" * 10),
        _message(2, MessageRole.ASSISTANT, "b" * 10),
        _message(3, MessageRole.USER, "c" * 50),
        _message(4, MessageRole.ASSISTANT, "d" * 50),
        _message(5, MessageRole.USER, "e" * 10),
        _message(6, MessageRole.ASSISTANT, "f" * 10),
    ]
    first = assemble_conversation_context(
        conversation_id=1, messages=messages, max_turns=2, max_chars=45, max_message_chars=20
    )
    second = assemble_conversation_context(
        conversation_id=1, messages=messages, max_turns=2, max_chars=45, max_message_chars=20
    )
    assert first == second
    assert first.char_count <= 45
    assert first.turn_count == 1
    assert [item.content for item in first.messages] == ["e" * 10, "f" * 10]
    assert first.truncated is True


def test_ask_and_sse_receive_only_previous_complete_owner_scoped_context(tmp_path) -> None:
    client, _app, settings, _auth, runtime, csrf = authed_client(tmp_path)
    conversation = create_conversation(client, csrf)
    database = ConversationDatabase(settings.conversation_database_path)
    connection = database.connect()
    try:
        messages = MessageRepository(connection)
        messages.append(
            owner_user_id=1,
            conversation_id=conversation["id"],
            role=MessageRole.USER,
            content="embedding?",
        )
        messages.append(
            owner_user_id=1,
            conversation_id=conversation["id"],
            role=MessageRole.ASSISTANT,
            content="BGE-small",
        )
    finally:
        connection.close()
    response = client.post(
        ASK_URL,
        json={"question": "那它和 E5 比较呢？", "conversation_id": conversation["id"]},
        headers=ask_headers(csrf),
    )
    assert response.status_code == 200
    context = runtime.service.last_conversation_context
    assert context is not None
    assert context.render() == "USER: embedding?\nASSISTANT: BGE-small"
    assert "那它和 E5 比较呢？" not in context.render()

    stream = client.post(
        STREAM_URL,
        json={"question": "继续比较", "conversation_id": conversation["id"]},
        headers=ask_headers(csrf),
    )
    assert stream.status_code == 200
    assert runtime.service.last_conversation_context is not None
    assert "继续比较" not in runtime.service.last_conversation_context.render()


def test_no_conversation_keeps_single_turn_service_contract(tmp_path) -> None:
    client, _app, _settings, _auth, runtime, csrf = authed_client(tmp_path)
    response = client.post(
        ASK_URL, json={"question": "independent"}, headers=ask_headers(csrf)
    )
    assert response.status_code == 200
    assert runtime.service.last_conversation_context is None
