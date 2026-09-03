"""Phase 15B bounded context assembly and API lifecycle tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.test_auth_api import ASK_URL, STREAM_URL, ask_headers, authed_client
from tests.test_conversation_api import create_conversation
from zglab_rag.conversation.context import (
    ConversationContext,
    ConversationContextMessage,
    assemble_conversation_context,
)
from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import Message, MessageRole
from zglab_rag.conversation.relevance import select_relevant_turns
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
        max_chars=300,
        max_message_chars=30,
    )
    assert [message.content for message in context.messages] == [
        "old question",
        "old answer",
        "recent question",
        "recent answer",
    ]
    assert context.turn_count == 2
    assert "RECENT TURNS" in context.render()
    assert "not evidence" in context.render()
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
        conversation_id=1, messages=messages, max_turns=2, max_chars=120, max_message_chars=20
    )
    second = assemble_conversation_context(
        conversation_id=1, messages=messages, max_turns=2, max_chars=120, max_message_chars=20
    )
    assert first == second
    assert first.char_count <= 120
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
    assert "RECENT TURNS" in context.render()
    assert "USER: embedding?\nASSISTANT: BGE-small" in context.render()
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
    response = client.post(ASK_URL, json={"question": "independent"}, headers=ask_headers(csrf))
    assert response.status_code == 200
    assert runtime.service.last_conversation_context is None


def test_context_relevance_is_deterministic_nonduplicating_and_utf8_bounded() -> None:
    messages = [
        _message(1, MessageRole.USER, "讨论 Python 发布计划"),
        _message(2, MessageRole.ASSISTANT, "Python 计划在这里"),
        _message(3, MessageRole.USER, "无关的旅行安排"),
        _message(4, MessageRole.ASSISTANT, "无关回答"),
        _message(5, MessageRole.USER, "最近的 Python 讨论"),
        _message(6, MessageRole.ASSISTANT, "recent answer"),
    ]
    relevant = select_relevant_turns(
        question="Python 发布是什么？",
        messages=messages,
        recent_message_ids={5, 6},
        max_turns=2,
    )
    assert [(user.id, assistant.id) for user, assistant in relevant] == [(1, 2)]
    context = assemble_conversation_context(
        conversation_id=1,
        messages=messages,
        max_turns=1,
        max_chars=800,
        max_message_chars=100,
        max_bytes=800,
        summary="中文摘要" * 40,
        summary_max_chars=80,
        relevant_messages=relevant,
        relevant_max_chars=80,
    )
    rendered = context.render()
    assert "CONVERSATION SUMMARY" in rendered
    assert "RELEVANT HISTORICAL TURNS" in rendered
    assert "RECENT TURNS" in rendered
    assert len(rendered) <= 800
    assert len(rendered.encode("utf-8")) <= 800
    query = context.retrieval_query("中文问题", max_chars=120, max_bytes=120)
    assert len(query) <= 120
    assert len(query.encode("utf-8")) <= 120


def test_summary_cannot_displace_newest_complete_turn() -> None:
    context = assemble_conversation_context(
        conversation_id=1,
        messages=[
            _message(1, MessageRole.USER, "latest user"),
            _message(2, MessageRole.ASSISTANT, "latest assistant"),
        ],
        max_turns=4,
        max_chars=250,
        max_message_chars=100,
        max_bytes=750,
        summary="summary " * 100,
        summary_max_chars=1600,
    )
    assert [message.content for message in context.recent_messages] == [
        "latest user",
        "latest assistant",
    ]
    assert context.truncated


def test_long_summary_and_relevance_still_keep_both_newest_turn_roles() -> None:
    latest_user = "u" * 1000
    latest_assistant = "a" * 2000
    relevant = [
        (
            _message(1, MessageRole.USER, "r" * 1000),
            _message(2, MessageRole.ASSISTANT, "s" * 1000),
        )
    ]
    context = assemble_conversation_context(
        conversation_id=1,
        messages=[
            _message(1, MessageRole.USER, "old"),
            _message(2, MessageRole.ASSISTANT, "old answer"),
            _message(3, MessageRole.USER, latest_user),
            _message(4, MessageRole.ASSISTANT, latest_assistant),
        ],
        max_turns=2,
        max_chars=3000,
        max_message_chars=2000,
        max_bytes=9000,
        summary="summary" * 300,
        summary_max_chars=1600,
        relevant_messages=relevant,
        relevant_max_chars=1200,
    )
    assert context.recent_turn_count >= 1
    assert context.relevant_messages == ()
    assert [message.role for message in context.recent_messages[-2:]] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
    ]
    assert all(message.content for message in context.recent_messages[-2:])
    assert len(context.render()) <= 3000
    assert len(context.render().encode("utf-8")) <= 9000


def test_retrieval_query_preserves_full_current_question_with_char_budget() -> None:
    context = ConversationContext(
        conversation_id=1,
        messages=(ConversationContextMessage(MessageRole.USER, "history " * 500),),
    )
    question = "q" * 900
    query = context.retrieval_query(question, max_chars=3000, max_bytes=9000)
    assert query.endswith(question)
    assert len(query) <= 3000
    assert len(query.encode("utf-8")) <= 9000


def test_retrieval_query_preserves_full_current_question_with_utf8_budget() -> None:
    context = ConversationContext(
        conversation_id=1,
        messages=(ConversationContextMessage(MessageRole.USER, "历史" * 1300),),
    )
    question = "当前问题" * 200
    query = context.retrieval_query(question, max_chars=3000, max_bytes=9000)
    assert query.endswith(question)
    assert len(query) <= 3000
    assert len(query.encode("utf-8")) <= 9000


def test_retrieval_query_uses_full_question_only_when_question_exceeds_budget() -> None:
    context = ConversationContext(
        conversation_id=1,
        messages=(ConversationContextMessage(MessageRole.USER, "history"),),
    )
    question = "q" * 1001
    assert context.retrieval_query(question, max_chars=1000, max_bytes=1000) == question
