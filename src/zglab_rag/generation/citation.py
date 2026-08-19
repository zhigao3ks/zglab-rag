from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

from zglab_rag.generation.contracts import (
    AnswerSource,
    EvidenceItem,
    GeneratedAnswer,
)

_EVIDENCE_ID = re.compile(r"^E[1-9][0-9]*$")


class CitationValidation(BaseModel):
    violations: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _dedupe(ids: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for citation in ids:
        if citation not in seen:
            seen.append(citation)
    return seen


def validate_generated_answer(
    answer: GeneratedAnswer,
    evidence: Sequence[EvidenceItem],
) -> CitationValidation:
    """Deterministically check citations against the evidence of this request.

    Hardened rules: when answering, claims are mandatory and every non-empty
    claim must carry at least one valid citation — "at least one cited claim"
    never grounds a whole answer. The cited evidence set is the deterministic
    union of validated claim citations; LLM top-level citations are not
    trusted and, when present, must equal that union exactly. Insufficient
    answers must not fabricate claims or citations.
    """
    allowed = {item.evidence_id for item in evidence}
    violations: list[str] = []

    if answer.insufficient_evidence:
        if answer.claims:
            violations.append("insufficient_evidence=true must not contain claims")
        if answer.citations:
            violations.append("insufficient_evidence=true must not contain citations")
        return CitationValidation(violations=violations)

    if not answer.claims:
        violations.append(
            "answered output must contain at least one claim; "
            "top-level citations alone cannot ground free-form answer text"
        )

    claim_citations: list[str] = []
    for index, claim in enumerate(answer.claims, start=1):
        if not claim.citations:
            violations.append(f"claim {index} has no citation")
            continue
        valid_for_claim = 0
        for citation in claim.citations:
            if not _EVIDENCE_ID.match(citation):
                violations.append(f"citation '{citation}' in claim {index} has an illegal format")
            elif citation not in allowed:
                violations.append(
                    f"citation '{citation}' in claim {index} is not part of this context"
                )
            else:
                claim_citations.append(citation)
                valid_for_claim += 1
        if valid_for_claim == 0:
            violations.append(f"claim {index} has no valid citation")

    union = _dedupe(claim_citations)
    if answer.claims and not union:
        violations.append("no valid citation grounds the answer")

    if answer.citations:
        for citation in answer.citations:
            if not _EVIDENCE_ID.match(citation):
                violations.append(
                    f"citation '{citation}' in answer citations has an illegal format"
                )
        if set(answer.citations) != set(union):
            violations.append(
                "answer citations must equal the union of claim citations "
                f"(expected {union}, got {answer.citations})"
            )

    return CitationValidation(violations=violations, cited_evidence_ids=union)


def resolve_sources(
    evidence_ids: Sequence[str],
    evidence: Sequence[EvidenceItem],
) -> list[AnswerSource]:
    """Map validated short evidence ids back to full public provenance."""
    by_id = {item.evidence_id: item for item in evidence}
    return [
        AnswerSource(
            evidence_id=evidence_id,
            source_id=item.source_id,
            document_id=item.document_id,
            chunk_id=item.chunk_id,
            title=item.title,
            source_path=item.source_path,
            section_path=list(item.section_path),
            score=item.score,
        )
        for evidence_id in evidence_ids
        if (item := by_id.get(evidence_id)) is not None
    ]
