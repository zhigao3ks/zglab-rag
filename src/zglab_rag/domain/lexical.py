from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LexicalProfile:
    profile_id: str
    tokenizer: str
    title_weight: float
    section_weight: float
    content_weight: float
    config_version: int
    config_hash: str

    @classmethod
    def create(
        cls,
        *,
        tokenizer: str,
        title_weight: float,
        section_weight: float,
        content_weight: float,
        config_version: int = 1,
    ) -> LexicalProfile:
        values = {
            "config_version": config_version,
            "content_weight": float(content_weight),
            "section_weight": float(section_weight),
            "title_weight": float(title_weight),
            "tokenizer": tokenizer,
        }
        canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
        config_hash = hashlib.sha256(canonical.encode()).hexdigest()
        return cls(
            profile_id=f"lp_{config_hash}",
            config_hash=config_hash,
            **values,
        )


DEFAULT_LEXICAL_PROFILE = LexicalProfile.create(
    tokenizer="trigram",
    title_weight=1.0,
    section_weight=1.0,
    content_weight=1.0,
)
