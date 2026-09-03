"""Deterministic lexical relevance scoring for historical turns.

No embeddings, no LLM, no external services — pure Python text matching.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from zglab_rag.conversation.models import Message, MessageRole


def _normalize_text(text: str) -> str:
    """Unicode NFKC normalize + lowercase Latin."""
    return unicodedata.normalize("NFKC", text).lower()


def _tokenize(text: str) -> set[str]:
    """Extract English/alphanumeric tokens + CJK bigrams."""
    normalized = _normalize_text(text)

    # English/alphanumeric tokens
    latin_tokens = set(re.findall(r"[a-z0-9]+", normalized))

    # CJK bigrams (Chinese/Japanese/Korean character pairs)
    cjk_chars = re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]", normalized)
    bigrams = set()
    for i in range(len(cjk_chars) - 1):
        bigrams.add(cjk_chars[i] + cjk_chars[i + 1])

    return latin_tokens | bigrams


def _compute_relevance_score(question: str, turn_text: str) -> int:
    """Lexical overlap score: question tokens ∩ turn tokens."""
    question_tokens = _tokenize(question)
    turn_tokens = _tokenize(turn_text)

    if not question_tokens or not turn_tokens:
        return 0

    return len(question_tokens & turn_tokens)


def select_relevant_turns(
    *,
    question: str,
    messages: Iterable[Message],
    recent_message_ids: set[int],
    max_turns: int,
) -> list[tuple[Message, Message]]:
    """Select historical turns with positive relevance to the question.

    Algorithm:
    1. Build complete USER/ASSISTANT pairs from messages
    2. Exclude pairs that are already in recent window (by message id)
    3. Score each pair by lexical overlap with question
    4. Keep only pairs with score > 0
    5. Sort by score (desc), then by recency (newer first) for ties
    6. Take top max_turns
    7. Return in chronological order for rendering

    Returns: list of (user_message, assistant_message) tuples
    """
    if max_turns <= 0:
        return []

    message_list = list(messages)

    # Build complete turns
    complete_turns: list[tuple[Message, Message]] = []
    i = 0
    while i + 1 < len(message_list):
        user_msg = message_list[i]
        assistant_msg = message_list[i + 1]

        if user_msg.role == MessageRole.USER and assistant_msg.role == MessageRole.ASSISTANT:
            # Skip if either message is in recent window
            if user_msg.id not in recent_message_ids and assistant_msg.id not in recent_message_ids:
                complete_turns.append((user_msg, assistant_msg))
            i += 2
        else:
            i += 1

    if not complete_turns:
        return []

    # Score each turn
    scored: list[tuple[int, int, Message, Message]] = []
    for user_msg, assistant_msg in complete_turns:
        turn_text = user_msg.content + " " + assistant_msg.content
        score = _compute_relevance_score(question, turn_text)

        if score > 0:
            # Use assistant message id for recency tiebreaker (higher = newer)
            scored.append((score, assistant_msg.id, user_msg, assistant_msg))

    if not scored:
        return []

    # Sort by score (desc), then by message id (desc for newer first)
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    # Take top max_turns
    selected = scored[:max_turns]

    # Sort by message id (asc) for chronological rendering
    selected.sort(key=lambda x: x[1])

    return [(user_msg, assistant_msg) for _, _, user_msg, assistant_msg in selected]
