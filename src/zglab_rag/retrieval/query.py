from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreparedLexicalQuery:
    match_expression: str | None
    applicable: bool
    reason: str | None = None


def prepare_lexical_query(query: str) -> PreparedLexicalQuery:
    normalized = " ".join(query.split())
    tokens = [token for token in re.findall(r"\w+", normalized) if len(token) >= 3]
    if not tokens:
        return PreparedLexicalQuery(
            match_expression=None,
            applicable=False,
            reason="trigram tokenizer requires at least one 3-character term",
        )
    unique_tokens = list(dict.fromkeys(tokens))
    expression = " OR ".join(f'"{token}"' for token in unique_tokens)
    return PreparedLexicalQuery(match_expression=expression, applicable=True)
