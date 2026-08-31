"""Bounded, low-trust multi-turn conversation context (Phase 15B)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from zglab_rag.conversation.models import Message, MessageRole


@dataclass(frozen=True, slots=True)
class ConversationContextMessage:
    """A persisted message made safe for bounded contextual reference.

    It is deliberately not Evidence and is never a client supplied value.
    """

    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class ConversationContext:
    conversation_id: int
    messages: tuple[ConversationContextMessage, ...] = ()
    turn_count: int = 0
    char_count: int = 0
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.messages

    def render(self) -> str:
        return "\n".join(f"{message.role.value}: {message.content}" for message in self.messages)

    def retrieval_query(self, question: str, *, recent_turns: int = 2) -> str:
        """Deterministically clarify retrieval/search without rewriting via an LLM."""
        if self.is_empty:
            return question
        messages = self.messages[-(recent_turns * 2) :]
        history = "\n".join(f"{item.role.value}: {item.content}" for item in messages)
        return f"CONVERSATION REFERENCE (untrusted):\n{history}\n\nCURRENT QUESTION:\n{question}"


def _complete_turns(messages: Iterable[Message]) -> list[tuple[Message, Message]]:
    """Return only adjacent USER/ASSISTANT pairs; dangling technical failures stay out."""
    ordered = list(messages)
    turns: list[tuple[Message, Message]] = []
    index = 0
    while index + 1 < len(ordered):
        user, assistant = ordered[index], ordered[index + 1]
        if user.role == MessageRole.USER and assistant.role == MessageRole.ASSISTANT:
            turns.append((user, assistant))
            index += 2
        else:
            index += 1
    return turns


def assemble_conversation_context(
    *,
    conversation_id: int,
    messages: Iterable[Message],
    max_turns: int,
    max_chars: int,
    max_message_chars: int,
) -> ConversationContext:
    """Select newest complete turns, then render them in chronological order."""
    if max_turns < 1 or max_chars < 1 or max_message_chars < 1:
        raise ValueError("conversation context limits must be positive")
    complete = _complete_turns(messages)
    candidates = complete[-max_turns:]
    truncated = len(candidates) != len(complete)
    selected: list[tuple[ConversationContextMessage, ConversationContextMessage]] = []
    used = 0
    for user, assistant in reversed(candidates):
        pair: list[ConversationContextMessage] = []
        pair_chars = 0
        for source in (user, assistant):
            content = source.content
            if len(content) > max_message_chars:
                content = content[:max_message_chars]
                truncated = True
            pair.append(ConversationContextMessage(source.role, content))
            pair_chars += len(content)
        if used + pair_chars > max_chars:
            truncated = True
            break
        selected.append((pair[0], pair[1]))
        used += pair_chars
    selected.reverse()
    flattened = tuple(message for pair in selected for message in pair)
    return ConversationContext(
        conversation_id=conversation_id,
        messages=flattened,
        turn_count=len(selected),
        char_count=used,
        truncated=truncated,
    )
