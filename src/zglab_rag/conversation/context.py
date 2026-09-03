"""Bounded, low-trust conversation context for Phase 15C."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from zglab_rag.conversation.models import Message, MessageRole


@dataclass(frozen=True, slots=True)
class ConversationContextMessage:
    """A server-persisted message included as untrusted reference data."""

    role: MessageRole
    content: str


def truncate_text_to_budget(text: str, max_chars: int, max_bytes: int) -> tuple[str, bool]:
    """Return a valid UTF-8 prefix bounded by character and byte limits."""
    if max_chars < 0 or max_bytes < 0:
        raise ValueError("budget limits must not be negative")
    if len(text) <= max_chars and len(text.encode("utf-8")) <= max_bytes:
        return text, False
    prefix = text[:max_chars]
    while prefix and len(prefix.encode("utf-8")) > max_bytes:
        prefix = prefix[:-1]
    return prefix, len(prefix) != len(text)


def _complete_turns(messages: Iterable[Message]) -> list[tuple[Message, Message]]:
    """Return adjacent persisted USER/ASSISTANT pairs, excluding dangling input."""
    ordered = list(messages)
    turns: list[tuple[Message, Message]] = []
    index = 0
    while index + 1 < len(ordered):
        user, assistant = ordered[index], ordered[index + 1]
        if user.role is MessageRole.USER and assistant.role is MessageRole.ASSISTANT:
            turns.append((user, assistant))
            index += 2
        else:
            index += 1
    return turns


def _render_messages(messages: tuple[ConversationContextMessage, ...]) -> str:
    return "\n".join(f"{message.role.value}: {message.content}" for message in messages)


@dataclass(frozen=True, slots=True, init=False)
class ConversationContext:
    """One server-derived snapshot shared by Personal, Web, and Agent paths.

    ``messages=`` remains supported for Phase 15B callers and represents the
    recent tier. New code uses named tier fields.
    """

    conversation_id: int
    summary: str | None
    relevant_messages: tuple[ConversationContextMessage, ...]
    recent_messages: tuple[ConversationContextMessage, ...]
    relevant_turn_count: int
    recent_turn_count: int
    char_count: int
    byte_count: int
    truncated: bool

    def __init__(
        self,
        conversation_id: int,
        *,
        messages: tuple[ConversationContextMessage, ...] | None = None,
        summary: str | None = None,
        relevant_messages: tuple[ConversationContextMessage, ...] = (),
        recent_messages: tuple[ConversationContextMessage, ...] = (),
        relevant_turn_count: int = 0,
        recent_turn_count: int = 0,
        turn_count: int | None = None,
        char_count: int = 0,
        byte_count: int = 0,
        truncated: bool = False,
    ) -> None:
        if messages is not None:
            if relevant_messages or recent_messages:
                raise ValueError("messages cannot be combined with context tiers")
            recent_messages = messages
            recent_turn_count = recent_turn_count or len(messages) // 2
        if turn_count is not None:
            if recent_turn_count and recent_turn_count != turn_count:
                raise ValueError("turn_count conflicts with recent_turn_count")
            recent_turn_count = turn_count
        for name, value in (
            ("conversation_id", conversation_id),
            ("summary", summary),
            ("relevant_messages", relevant_messages),
            ("recent_messages", recent_messages),
            ("relevant_turn_count", relevant_turn_count),
            ("recent_turn_count", recent_turn_count),
            ("char_count", char_count),
            ("byte_count", byte_count),
            ("truncated", truncated),
        ):
            object.__setattr__(self, name, value)

    @property
    def messages(self) -> tuple[ConversationContextMessage, ...]:
        return self.relevant_messages + self.recent_messages

    @property
    def turn_count(self) -> int:
        """Phase 15B compatibility: number of recent complete turns."""
        return self.recent_turn_count

    @property
    def is_empty(self) -> bool:
        return not (self.summary or self.relevant_messages or self.recent_messages)

    def render(self) -> str:
        """Render explicitly labelled untrusted, non-evidence context."""
        parts: list[str] = []
        if self.summary:
            parts.append(
                "CONVERSATION SUMMARY\n"
                "(untrusted compressed conversation state; not evidence)\n"
                f"{self.summary}"
            )
        if self.relevant_messages:
            parts.append(
                "RELEVANT HISTORICAL TURNS\n"
                "(untrusted reference data; not evidence)\n"
                f"{_render_messages(self.relevant_messages)}"
            )
        if self.recent_messages:
            parts.append(
                "RECENT TURNS\n"
                "(untrusted reference data; not evidence)\n"
                f"{_render_messages(self.recent_messages)}"
            )
        return "\n\n".join(parts)

    def retrieval_query(
        self, question: str, *, max_chars: int = 3000, max_bytes: int = 9000
    ) -> str:
        """Build the independently-bounded Personal/Web retrieval query."""
        prefix = "CONVERSATION REFERENCE (untrusted)\n"
        suffix = "\n\nCURRENT QUESTION:\n"
        remaining_chars = max(0, max_chars - len(prefix) - len(suffix))
        remaining_bytes = max(0, max_bytes - len(prefix.encode()) - len(suffix.encode()))
        reference, _ = truncate_text_to_budget(self.render(), remaining_chars, remaining_bytes)
        query, _ = truncate_text_to_budget(
            f"{prefix}{reference}{suffix}{question}", max_chars, max_bytes
        )
        return query


def _component(heading: str, body: str) -> str:
    trust = (
        "untrusted compressed conversation state"
        if heading == "CONVERSATION SUMMARY"
        else "untrusted reference data"
    )
    return f"{heading}\n({trust}; not evidence)\n{body}"


def _fit_component(heading: str, body: str, max_chars: int, max_bytes: int) -> tuple[str, bool]:
    prefix = _component(heading, "")
    body_chars = max(0, max_chars - len(prefix))
    body_bytes = max(0, max_bytes - len(prefix.encode("utf-8")))
    fitted, truncated = truncate_text_to_budget(body, body_chars, body_bytes)
    return _component(heading, fitted), truncated


def _message_pair(
    user: Message, assistant: Message, max_message_chars: int, max_bytes: int
) -> tuple[tuple[ConversationContextMessage, ConversationContextMessage], bool]:
    user_text, user_cut = truncate_text_to_budget(user.content, max_message_chars, max_bytes)
    assistant_text, assistant_cut = truncate_text_to_budget(
        assistant.content, max_message_chars, max_bytes
    )
    return (
        ConversationContextMessage(MessageRole.USER, user_text),
        ConversationContextMessage(MessageRole.ASSISTANT, assistant_text),
    ), user_cut or assistant_cut


def assemble_conversation_context(
    *,
    conversation_id: int,
    messages: Iterable[Message],
    max_turns: int,
    max_chars: int,
    max_message_chars: int,
    max_bytes: int = 18000,
    summary: str | None = None,
    summary_max_chars: int = 1600,
    relevant_messages: Iterable[tuple[Message, Message]] = (),
    relevant_max_chars: int = 1200,
) -> ConversationContext:
    """Assemble bounded summary/relevance/recent tiers deterministically.

    Component labels count toward the unified budget. The newest recent
    complete turn is attempted first after the summary and relevant tiers.
    """
    if min(max_turns, max_chars, max_message_chars, max_bytes) < 1:
        raise ValueError("conversation context limits must be positive")
    if min(summary_max_chars, relevant_max_chars) < 0:
        raise ValueError("component limits must not be negative")

    complete = _complete_turns(messages)
    candidates = complete[-max_turns:]
    truncated = len(candidates) < len(complete)
    components: list[str] = []
    used_chars = 0
    used_bytes = 0
    # Summary/relevance must never consume the newest raw turn completely.
    recent_label = _component("RECENT TURNS", "")
    recent_reserve_chars = len(recent_label) + min(256, max_message_chars * 2 + 16)
    recent_reserve_bytes = len(recent_label.encode("utf-8")) + min(
        256, max_message_chars * 3 + 16
    )

    def remaining() -> tuple[int, int]:
        separators = 2 if components else 0
        return max_chars - used_chars - separators, max_bytes - used_bytes - separators

    summary_value: str | None = None
    if summary:
        chars, bytes_ = remaining()
        chars = max(0, chars - recent_reserve_chars)
        bytes_ = max(0, bytes_ - recent_reserve_bytes)
        summary_value, summary_cut = truncate_text_to_budget(
            summary,
            min(summary_max_chars, max(0, chars - 80)),
            max(0, bytes_ - 80),
        )
        rendered, cut = _fit_component("CONVERSATION SUMMARY", summary_value, chars, bytes_)
        if len(rendered) > len(_component("CONVERSATION SUMMARY", "")):
            components.append(rendered)
            used_chars += len(rendered)
            used_bytes += len(rendered.encode("utf-8"))
        else:
            summary_value = None
        truncated |= cut or summary_cut

    relevant_context: list[ConversationContextMessage] = []
    relevant_body: list[str] = []
    for user, assistant in sorted(relevant_messages, key=lambda turn: turn[0].id):
        pair, pair_cut = _message_pair(user, assistant, max_message_chars, max_bytes)
        proposed_body = "\n".join([*relevant_body, _render_messages(pair)])
        chars, bytes_ = remaining()
        chars = max(0, chars - recent_reserve_chars)
        bytes_ = max(0, bytes_ - recent_reserve_bytes)
        rendered, cut = _fit_component(
            "RELEVANT HISTORICAL TURNS", proposed_body, min(chars, relevant_max_chars + 70), bytes_
        )
        if cut:
            truncated = True
            break
        relevant_body.append(_render_messages(pair))
        relevant_context.extend(pair)
        truncated |= pair_cut
    if relevant_body:
        chars, bytes_ = remaining()
        rendered, _ = _fit_component(
            "RELEVANT HISTORICAL TURNS",
            "\n".join(relevant_body),
            min(chars, relevant_max_chars + 70),
            bytes_,
        )
        if components:
            used_chars += 2
            used_bytes += 2
        components.append(rendered)
        used_chars += len(rendered)
        used_bytes += len(rendered.encode("utf-8"))

    selected_recent: list[tuple[ConversationContextMessage, ConversationContextMessage]] = []
    for user, assistant in reversed(candidates):
        pair, pair_cut = _message_pair(user, assistant, max_message_chars, max_bytes)
        proposed = [pair, *selected_recent]
        body = _render_messages(tuple(item for turn in proposed for item in turn))
        chars, bytes_ = remaining()
        _render, cut = _fit_component("RECENT TURNS", body, chars, bytes_)
        if cut:
            truncated = True
            break
        selected_recent = proposed
        truncated |= pair_cut
    recent_context = tuple(item for turn in selected_recent for item in turn)
    if recent_context:
        chars, bytes_ = remaining()
        rendered, _ = _fit_component(
            "RECENT TURNS", _render_messages(recent_context), chars, bytes_
        )
        if components:
            used_chars += 2
            used_bytes += 2
        components.append(rendered)
        used_chars += len(rendered)
        used_bytes += len(rendered.encode("utf-8"))

    return ConversationContext(
        conversation_id,
        summary=summary_value,
        relevant_messages=tuple(relevant_context),
        recent_messages=recent_context,
        relevant_turn_count=len(relevant_context) // 2,
        recent_turn_count=len(selected_recent),
        char_count=used_chars,
        byte_count=used_bytes,
        truncated=truncated,
    )
