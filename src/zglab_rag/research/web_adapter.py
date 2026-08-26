"""Phase 12C adapter: ExternalEvidence → generation EvidenceItem.

This is the only bridge between the research pipeline and grounded
generation. It deliberately does NOT fake personal chunk identity: web
items keep origin=WEB, carry url/domain locators, and leave chunk
identifiers as None.

Citation namespace: internal research identity (W1/W2, request-stable
provenance) is mapped to the answer display namespace (E1/E2...) here,
deterministically in search-rank order. Clients only ever see E ids.
"""

from __future__ import annotations

from collections.abc import Sequence

from zglab_rag.domain.models import Visibility
from zglab_rag.generation.contracts import EvidenceItem, EvidenceOrigin
from zglab_rag.research.contracts import ExternalEvidence


def _internal_rank(evidence_id: str) -> int:
    """Numeric part of a W id, used for deterministic ordering."""
    return int(evidence_id[1:])


def adapt_external_evidence(
    items: Sequence[ExternalEvidence],
    *,
    max_items: int,
) -> list[EvidenceItem]:
    """Adapt fetched web evidence into generation evidence items.

    Ordering is deterministic: internal W ids in numeric order. Display ids
    are assigned E1..En in that order, so the W→E mapping is reproducible
    for identical research output.
    """
    adapted: list[EvidenceItem] = []
    for item in sorted(items, key=lambda entry: _internal_rank(entry.evidence_id)):
        if len(adapted) >= max_items:
            break
        adapted.append(
            EvidenceItem(
                evidence_id=f"E{len(adapted) + 1}",
                chunk_id=None,
                document_id=None,
                source_id=item.domain,
                source_path=item.url,
                title=item.title or item.domain,
                section_path=[],
                content=item.content,
                revision=None,
                visibility=Visibility.PUBLIC,
                rank=item.search_rank,
                score=0.0,
                origin=EvidenceOrigin.WEB,
                url=item.url,
                domain=item.domain,
            )
        )
    return adapted
