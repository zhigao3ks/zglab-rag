"""Phase 15C summary schema, repository, and fail-soft refresh tests."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_auth_api import (
    ASK_URL,
    STREAM_URL,
    ask_headers,
    build_app,
    login,
    provision_active_user,
)
from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import MessageRole
from zglab_rag.conversation.repositories import (
    ConversationRepository,
    ConversationSummaryRepository,
    MessageRepository,
)
from zglab_rag.conversation.summary import (
    ConversationSummaryService,
    SummaryConfig,
    _source_prompt,
)
from zglab_rag.generation.contracts import GenerationRequest, ProviderResponse
from zglab_rag.generation.errors import ProviderFailure


class _SummaryProvider:
    name = "test"
    model = "test"

    def __init__(self, response: str = '{"summary":"project constraint"}') -> None:
        self.response = response
        self.calls: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> ProviderResponse:
        self.calls.append(request)
        if self.response == "failure":
            raise ProviderFailure("provider down")
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self.response,
            latency_ms=1,
        )


def _repos(path: Path):
    connection = ConversationDatabase(path).connect()
    return connection, ConversationRepository(connection), MessageRepository(connection)


def _add_turns(messages: MessageRepository, conversation_id: int, count: int) -> None:
    for number in range(count):
        assert messages.append(
            owner_user_id=1,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            content=f"question {number}",
        )
        assert messages.append(
            owner_user_id=1,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            content=f"answer {number}",
        )


def test_v1_migrates_to_v2_without_losing_conversation_or_messages(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO schema_metadata VALUES ('schema_version', '1');
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL, title TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL
        );
        INSERT INTO conversations VALUES (1, 7, 'old', '2026-01-01T00:00:00+00:00',
            '2026-01-01T00:00:00+00:00');
        INSERT INTO messages VALUES (1, 1, 'USER', 'preserved', '2026-01-01T00:00:00+00:00');
        """
    )
    connection.close()

    migrated = ConversationDatabase(path).connect()
    try:
        assert ConversationDatabase.schema_version(migrated) == 2
        persisted = migrated.execute("SELECT content FROM messages WHERE id=1").fetchone()
        assert persisted[0] == "preserved"
        assert migrated.execute(
            "SELECT name FROM sqlite_master WHERE name='conversation_summaries'"
        ).fetchone()
    finally:
        migrated.close()


def test_summary_repository_enforces_owner_message_coverage_and_cascade(tmp_path: Path) -> None:
    connection, conversations, messages = _repos(tmp_path / "conversation.db")
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        user = messages.append(
            owner_user_id=1, conversation_id=conversation.id, role=MessageRole.USER, content="q"
        )
        assistant = messages.append(
            owner_user_id=1,
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="a",
        )
        assert user and assistant
        summaries = ConversationSummaryRepository(connection)
        saved = summaries.upsert(
            owner_user_id=1,
            conversation_id=conversation.id,
            content="state",
            covered_through_message_id=assistant.id,
        )
        assert summaries.get(owner_user_id=2, conversation_id=conversation.id) is None
        try:
            summaries.upsert(
                owner_user_id=1,
                conversation_id=conversation.id,
                content="bad",
                covered_through_message_id=user.id,
            )
        except ValueError as exc:
            assert "ASSISTANT" in str(exc)
        else:
            raise AssertionError("USER coverage was accepted")
        assert saved.covered_through_message_id == assistant.id
        later_user = messages.append(
            owner_user_id=1,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="later q",
        )
        later_assistant = messages.append(
            owner_user_id=1,
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="later a",
        )
        assert later_user and later_assistant
        summaries.upsert(
            owner_user_id=1,
            conversation_id=conversation.id,
            content="newer state",
            covered_through_message_id=later_assistant.id,
        )
        try:
            summaries.upsert(
                owner_user_id=1,
                conversation_id=conversation.id,
                content="rollback",
                covered_through_message_id=assistant.id,
            )
        except ValueError as exc:
            assert "rollback" in str(exc).lower()
        else:
            raise AssertionError("summary coverage rollback was accepted")
        assert conversations.delete(owner_user_id=1, conversation_id=conversation.id)
        assert connection.execute("SELECT COUNT(*) FROM conversation_summaries").fetchone()[0] == 0
    finally:
        connection.close()


def test_incremental_summary_respects_recent_window_and_fail_soft(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection, conversations, messages = _repos(path)
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        _add_turns(messages, conversation.id, 8)
    finally:
        connection.close()

    provider = _SummaryProvider()
    service = ConversationSummaryService(
        provider=provider,
        database=ConversationDatabase(path),
        config=SummaryConfig(enabled=True, trigger_new_turns=4, max_batch_turns=8),
    )
    assert service.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert len(provider.calls) == 1
    assert provider.calls[0].allowed_evidence_ids == ()
    assert "question 0" in provider.calls[0].user_prompt
    assert "question 4" not in provider.calls[0].user_prompt

    connection, _conversations, _messages = _repos(path)
    try:
        summary = ConversationSummaryRepository(connection).get(
            owner_user_id=1, conversation_id=conversation.id
        )
        assert summary and summary.covered_through_message_id == 8
        _add_turns(_messages, conversation.id, 1)
    finally:
        connection.close()
    assert not service.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert len(provider.calls) == 1

    failed = ConversationSummaryService(
        provider=_SummaryProvider("not json"),
        database=ConversationDatabase(path),
        config=SummaryConfig(enabled=True, trigger_new_turns=1),
    )
    assert not failed.refresh_summary(owner_user_id=1, conversation_id=conversation.id)


def test_summary_source_budget_controls_prompt_and_coverage(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection, conversations, messages = _repos(path)
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        _add_turns(messages, conversation.id, 8)
    finally:
        connection.close()
    provider = _SummaryProvider()
    service = ConversationSummaryService(
        provider=provider,
        database=ConversationDatabase(path),
        config=SummaryConfig(
            enabled=True,
            trigger_new_turns=4,
            max_batch_turns=8,
            source_max_chars=120,
            source_max_bytes=1000,
        ),
    )
    assert service.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert len(provider.calls) == 1
    assert "question 0" in provider.calls[0].user_prompt
    assert "question 1" in provider.calls[0].user_prompt
    assert "question 2" not in provider.calls[0].user_prompt
    connection, _conversations, _messages = _repos(path)
    try:
        summary = ConversationSummaryRepository(connection).get(
            owner_user_id=1, conversation_id=conversation.id
        )
        assert summary and summary.covered_through_message_id == 4
    finally:
        connection.close()


def test_summary_skips_when_source_cannot_include_one_complete_turn(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection, conversations, messages = _repos(path)
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        _add_turns(messages, conversation.id, 8)
    finally:
        connection.close()
    provider = _SummaryProvider()
    service = ConversationSummaryService(
        provider=provider,
        database=ConversationDatabase(path),
        config=SummaryConfig(
            enabled=True,
            trigger_new_turns=4,
            source_max_chars=35,
            source_max_bytes=35,
        ),
    )
    assert not service.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert provider.calls == []


def test_summary_disabled_and_provider_failure_preserve_existing_summary(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection, conversations, messages = _repos(path)
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        _add_turns(messages, conversation.id, 8)
        existing = ConversationSummaryRepository(connection).upsert(
            owner_user_id=1,
            conversation_id=conversation.id,
            content="existing state",
            covered_through_message_id=2,
        )
    finally:
        connection.close()
    disabled_provider = _SummaryProvider()
    disabled = ConversationSummaryService(
        provider=disabled_provider,
        database=ConversationDatabase(path),
        config=SummaryConfig(enabled=False),
    )
    assert not disabled.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert disabled_provider.calls == []
    failed = ConversationSummaryService(
        provider=_SummaryProvider("failure"),
        database=ConversationDatabase(path),
        config=SummaryConfig(enabled=True, trigger_new_turns=1),
    )
    assert not failed.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    connection, _conversations, _messages = _repos(path)
    try:
        current = ConversationSummaryRepository(connection).get(
            owner_user_id=1, conversation_id=conversation.id
        )
        assert current == existing
    finally:
        connection.close()


def test_previous_summary_and_max_batch_are_bound_to_included_turns(tmp_path: Path) -> None:
    path = tmp_path / "conversation.db"
    connection, conversations, messages = _repos(path)
    try:
        conversation = conversations.create(owner_user_id=1, title="summary")
        _add_turns(messages, conversation.id, 8)
        existing = ConversationSummaryRepository(connection).upsert(
            owner_user_id=1,
            conversation_id=conversation.id,
            content="previous compact state",
            covered_through_message_id=2,
        )
        all_messages = messages.list_for_conversation(
            owner_user_id=1, conversation_id=conversation.id
        )
    finally:
        connection.close()
    prompt, included = _source_prompt(
        existing.content,
        [(all_messages[2], all_messages[3])],
        max_chars=1000,
        max_bytes=3000,
    )
    assert "previous compact state" in prompt
    assert included == [(all_messages[2], all_messages[3])]
    provider = _SummaryProvider()
    service = ConversationSummaryService(
        provider=provider,
        database=ConversationDatabase(path),
        config=SummaryConfig(enabled=True, trigger_new_turns=1, max_batch_turns=2),
    )
    assert service.refresh_summary(owner_user_id=1, conversation_id=conversation.id)
    assert "question 1" in provider.calls[0].user_prompt
    assert "question 3" not in provider.calls[0].user_prompt


def test_plain_and_sse_keep_public_contract_while_summary_refreshes(tmp_path: Path) -> None:
    provider = _SummaryProvider()
    app, settings, auth_runtime, runtime = build_app(
        tmp_path,
        conversation_summary_enabled=True,
        conversation_summary_trigger_new_turns=4,
    )
    runtime.llm_provider = provider
    provision_active_user(auth_runtime, settings)
    with TestClient(app) as client:
        csrf = login(client).json()["csrf_token"]
        created = client.post(
            "/api/v2/conversations", json={"title": "summary"}, headers=ask_headers(csrf)
        ).json()
        connection = ConversationDatabase(settings.conversation_database_path).connect()
        try:
            _add_turns(MessageRepository(connection), created["id"], 8)
        finally:
            connection.close()

        plain = client.post(
            ASK_URL,
            json={"question": "continue", "conversation_id": created["id"]},
            headers=ask_headers(csrf),
        )
        assert plain.status_code == 200
        assert "summary" not in plain.json()
        for _ in range(30):
            if provider.calls:
                break
            time.sleep(0.02)
        assert len(provider.calls) == 1

        streamed = client.post(
            STREAM_URL,
            json={"question": "continue again", "conversation_id": created["id"]},
            headers=ask_headers(csrf),
        )
        assert streamed.status_code == 200
        assert "event: completed" in streamed.text
