from __future__ import annotations

from typing import Protocol


class PassageEvidence(Protocol):
    title: str
    section_path: list[str]
    content: str


def compose_passage_context(evidence: PassageEvidence) -> str:
    section = " > ".join(evidence.section_path) or "(root)"
    return f"Title: {evidence.title}\nSection: {section}\n\n{evidence.content}"
