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
        """Build a bounded query while preserving the full current question.

        Conversation history is optional reference data. The current question
        is the actual retrieval/search target and therefore receives budget
        priority. If an invalidly large direct caller exceeds the configured
        retrieval budget, the deterministic fallback is the complete question
        alone rather than a silently truncated question.
        """
        prefix = "CONVERSATION REFERENCE (untrusted)\n"
        suffix = "\n\nCURRENT QUESTION:\n"
        fixed_chars = len(prefix) + len(suffix)
        fixed_bytes = len(prefix.encode("utf-8")) + len(suffix.encode("utf-8"))
        question_chars = len(question)
        question_bytes = len(question.encode("utf-8"))
        if question_chars + fixed_chars > max_chars or question_bytes + fixed_bytes > max_bytes:
            return question
        reference, _ = truncate_text_to_budget(
            self.render(),
            max_chars - fixed_chars - question_chars,
            max_bytes - fixed_bytes - question_bytes,
        )
        return f"{prefix}{reference}{suffix}{question}"


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


def _recent_component(
    pair: tuple[ConversationContextMessage, ConversationContextMessage],
) -> str:
    return _component("RECENT TURNS", _render_messages(pair))


def _fit_newest_pair(
    pair: tuple[ConversationContextMessage, ConversationContextMessage],
    *,
    max_chars: int,
    max_bytes: int,
) -> tuple[tuple[ConversationContextMessage, ConversationContextMessage], bool]:
    """Fit the newest turn into the whole context while retaining both roles.

    Settings guarantee budgets large enough for the labels plus one character
    per message. The loop only runs for the unusual case where the already
    per-message-bounded newest turn still exceeds the full context budget.
    """
    user, assistant = pair
    user_content = user.content or "…"
    assistant_content = assistant.content or "…"
    truncated = user_content != user.content or assistant_content != assistant.content
    while True:
        candidate = _recent_component(
            (
                ConversationContextMessage(MessageRole.USER, user_content),
                ConversationContextMessage(MessageRole.ASSISTANT, assistant_content),
            )
        )
        if len(candidate) <= max_chars and len(candidate.encode("utf-8")) <= max_bytes:
            return (
                ConversationContextMessage(MessageRole.USER, user_content),
                ConversationContextMessage(MessageRole.ASSISTANT, assistant_content),
            ), truncated
        if len(user_content) >= len(assistant_content) and len(user_content) > 1:
            user_content = user_content[:-1]
        elif len(assistant_content) > 1:
            assistant_content = assistant_content[:-1]
        else:
            # This is reachable only for direct callers using a budget smaller
            # than the configured minimum; retain the invariant as far as the
            # labels themselves allow instead of silently dropping the turn.
            return (
                ConversationContextMessage(MessageRole.USER, user_content),
                ConversationContextMessage(MessageRole.ASSISTANT, assistant_content),
            ), True
        truncated = True


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
    recent_message_ids = {message.id for turn in candidates for message in turn}
    truncated = len(candidates) < len(complete)
    if not candidates:
        return ConversationContext(conversation_id, truncated=truncated)

    newest_pair, newest_cut = _message_pair(
        *candidates[-1], max_message_chars, max_bytes
    )
    newest_pair, newest_fitted = _fit_newest_pair(
        newest_pair, max_chars=max_chars, max_bytes=max_bytes
    )
    newest_component = _recent_component(newest_pair)
    truncated |= newest_cut or newest_fitted
    # Reserve the exact rendered newest turn plus a possible separator before
    # it. Summary and relevant history can never borrow this reservation.
    reserve_chars = len(newest_component) + 2
    reserve_bytes = len(newest_component.encode("utf-8")) + 2
    components: list[str] = []
    used_chars = 0
    used_bytes = 0

    def available_before_recent() -> tuple[int, int]:
        separator = 2 if components else 0
        return (
            max_chars - used_chars - separator - reserve_chars,
            max_bytes - used_bytes - separator - reserve_bytes,
        )

    summary_value: str | None = None
    if summary:
        available_chars, available_bytes = available_before_recent()
        label = _component("CONVERSATION SUMMARY", "")
        summary_value, summary_cut = truncate_text_to_budget(
            summary,
            min(summary_max_chars, max(0, available_chars - len(label))),
            max(0, available_bytes - len(label.encode("utf-8"))),
        )
        if summary_value:
            rendered = _component("CONVERSATION SUMMARY", summary_value)
            components.append(rendered)
            used_chars += len(rendered)
            used_bytes += len(rendered.encode("utf-8"))
        else:
            summary_value = None
        truncated |= summary_cut

    relevant_context: list[ConversationContextMessage] = []
    relevant_body: list[str] = []
    for user, assistant in sorted(relevant_messages, key=lambda turn: turn[0].id):
        if user.id in recent_message_ids or assistant.id in recent_message_ids:
            continue
        pair, pair_cut = _message_pair(user, assistant, max_message_chars, max_bytes)
        candidate_body = "\n".join([*relevant_body, _render_messages(pair)])
        rendered = _component("RELEVANT HISTORICAL TURNS", candidate_body)
        available_chars, available_bytes = available_before_recent()
        component_limit_chars = min(available_chars, relevant_max_chars + 70)
        if (
            len(rendered) > component_limit_chars
            or len(rendered.encode("utf-8")) > available_bytes
        ):
            truncated = True
            break
        relevant_body.append(_render_messages(pair))
        relevant_context.extend(pair)
        truncated |= pair_cut
    if relevant_body:
        rendered = _component("RELEVANT HISTORICAL TURNS", "\n".join(relevant_body))
        if components:
            used_chars += 2
            used_bytes += 2
        components.append(rendered)
        used_chars += len(rendered)
        used_bytes += len(rendered.encode("utf-8"))

    selected_recent = [newest_pair]
    for user, assistant in reversed(candidates[:-1]):
        pair, pair_cut = _message_pair(user, assistant, max_message_chars, max_bytes)
        proposed = [pair, *selected_recent]
        rendered = _recent_component(tuple(item for turn in proposed for item in turn))
        prefix_separator = 2 if components else 0
        if (
            used_chars + prefix_separator + len(rendered) > max_chars
            or used_bytes + prefix_separator + len(rendered.encode("utf-8")) > max_bytes
        ):
            truncated = True
            break
        selected_recent = proposed
        truncated |= pair_cut
    recent_context = tuple(item for turn in selected_recent for item in turn)
    rendered_recent = _recent_component(recent_context)
    if components:
        used_chars += 2
        used_bytes += 2
    components.append(rendered_recent)
    used_chars += len(rendered_recent)
    used_bytes += len(rendered_recent.encode("utf-8"))

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
