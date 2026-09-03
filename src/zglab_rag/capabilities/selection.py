"""Deterministic product capability selection (Phase 12D).

Chooses exactly one primary capability per ask request. The policy is
deliberately small, auditable and non-LLM: there is no router model, no
intent classifier and no reasoning chain — Phase 14 (Agent Planner) may
replace this temporary product router, but 12D must stay deterministic
and cost-predictable.

Conservative rules:
- explicit ``mode=personal/web`` always wins;
- self-reference / personal identity and product-knowledge questions go to
  PERSONAL first (Personal Facts Integrity: web search must never rewrite
  the owner's own biography or replace the product's indexed documentation);
- clearly current / external-information questions go to WEB;
- anything ambiguous falls back to PERSONAL so ordinary questions never
  spend Search API budget.

When the web kill switch is off, an AUTO selection that would have picked
WEB degrades to PERSONAL (silent, safe, no cost); an EXPLICIT web request
keeps its WEB selection so the API layer can answer CAPABILITY_DISABLED
instead of silently switching behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from zglab_rag.capabilities.contracts import PERSONAL_KNOWLEDGE_CAPABILITY_ID
from zglab_rag.research.contracts import WEB_RESEARCH_CAPABILITY_ID


class AskMode(StrEnum):
    """Public request mode; validated server-side, no capability ids."""

    AUTO = "auto"
    PERSONAL = "personal"
    WEB = "web"
    AGENT = "agent"


class SelectionReason(StrEnum):
    """Auditable, single-code decision trace (never a reasoning chain)."""

    EXPLICIT_PERSONAL = "explicit_personal"
    EXPLICIT_WEB = "explicit_web"
    PERSONAL_SELF_REFERENCE = "personal_self_reference"
    PERSONAL_KNOWLEDGE_REFERENCE = "personal_knowledge_reference"
    CURRENT_INFORMATION = "current_information"
    DEFAULT_PERSONAL = "default_personal"
    WEB_DISABLED_FALLBACK_PERSONAL = "web_disabled_fallback_personal"


@dataclass(frozen=True, slots=True)
class CapabilitySelection:
    capability_id: str
    reason: SelectionReason


# Small, auditable pattern sets — intentionally NOT a giant keyword
# dictionary. Self-reference wins over current-information so personal
# identity questions never drift into web search.
_SELF_REFERENCE_MARKERS = (
    "我",
    "本人",
    "黄志高",
    "志高",
    "简历",
    "履历",
    "自我介绍",
)

# Product questions such as "ZGLab Personal AI Agent 当前有哪些核心能力？"
# can naturally contain a time marker ("当前"), but the authoritative answer
# is in the indexed public project documentation rather than on the web.
# Keep this deliberately small and product-specific so generic current-event
# queries retain their existing WEB behavior.
_PERSONAL_KNOWLEDGE_MARKERS = (
    "zglab",
    "personal ai agent",
    "personal knowledge assistant",
)

_CURRENT_INFORMATION_MARKERS = (
    "最新",
    "最近",
    "今天",
    "昨天",
    "最新发布",
    "最近发布",
    "latest",
    "current version",
)


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def select_capability(
    question: str,
    mode: AskMode,
    *,
    web_research_enabled: bool,
) -> CapabilitySelection:
    """Deterministically pick the single primary capability for one ask.

    Selection happens exactly once; there is no fallback loop between
    capabilities (Personal-insufficient -> Web is deliberately NOT
    implemented in 12D).
    """
    if mode == AskMode.PERSONAL:
        return CapabilitySelection(
            PERSONAL_KNOWLEDGE_CAPABILITY_ID, SelectionReason.EXPLICIT_PERSONAL
        )
    if mode == AskMode.WEB:
        # Kept WEB even while disabled: the API layer answers
        # CAPABILITY_DISABLED rather than silently switching capabilities.
        return CapabilitySelection(
            WEB_RESEARCH_CAPABILITY_ID, SelectionReason.EXPLICIT_WEB
        )

    normalized = question.strip()
    if _contains_any(normalized, _SELF_REFERENCE_MARKERS):
        return CapabilitySelection(
            PERSONAL_KNOWLEDGE_CAPABILITY_ID, SelectionReason.PERSONAL_SELF_REFERENCE
        )
    if _contains_any(normalized, _PERSONAL_KNOWLEDGE_MARKERS):
        return CapabilitySelection(
            PERSONAL_KNOWLEDGE_CAPABILITY_ID,
            SelectionReason.PERSONAL_KNOWLEDGE_REFERENCE,
        )
    if _contains_any(normalized, _CURRENT_INFORMATION_MARKERS):
        if web_research_enabled:
            return CapabilitySelection(
                WEB_RESEARCH_CAPABILITY_ID, SelectionReason.CURRENT_INFORMATION
            )
        return CapabilitySelection(
            PERSONAL_KNOWLEDGE_CAPABILITY_ID,
            SelectionReason.WEB_DISABLED_FALLBACK_PERSONAL,
        )
    return CapabilitySelection(
        PERSONAL_KNOWLEDGE_CAPABILITY_ID, SelectionReason.DEFAULT_PERSONAL
    )
