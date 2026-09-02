"""Phase 12D tests: deterministic product capability selection.

Locks the small, auditable, non-LLM router: explicit modes win, personal
self-reference outranks current-information (Personal Facts Integrity),
current/external intent selects web, ambiguity conservatively stays
personal, and the kill switch degrades auto-web silently while keeping
explicit-web visible for CAPABILITY_DISABLED handling upstream.
"""

from __future__ import annotations

import pytest

from zglab_rag.capabilities.contracts import PERSONAL_KNOWLEDGE_CAPABILITY_ID
from zglab_rag.capabilities.selection import (
    AskMode,
    SelectionReason,
    select_capability,
)
from zglab_rag.research.contracts import WEB_RESEARCH_CAPABILITY_ID


def _select(question: str, mode: str = "auto", *, enabled: bool = True):
    return select_capability(
        question, AskMode(mode), web_research_enabled=enabled
    )


class TestExplicitModes:
    @pytest.mark.parametrize("question", ["随便一个问题", "Python 最新版本是什么？"])
    def test_explicit_personal_always_personal(self, question: str) -> None:
        selection = _select(question, "personal")
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.EXPLICIT_PERSONAL

    @pytest.mark.parametrize("question", ["随便一个问题", "我做过什么项目？"])
    def test_explicit_web_always_web(self, question: str) -> None:
        selection = _select(question, "web")
        assert selection.capability_id == WEB_RESEARCH_CAPABILITY_ID
        assert selection.reason == SelectionReason.EXPLICIT_WEB

    def test_explicit_web_stays_web_while_disabled(self) -> None:
        # The API layer must see the WEB selection to answer
        # CAPABILITY_DISABLED instead of silently switching behavior.
        selection = _select("某个问题", "web", enabled=False)
        assert selection.capability_id == WEB_RESEARCH_CAPABILITY_ID


class TestAutoSelection:
    @pytest.mark.parametrize(
        "question",
        [
            "我做过哪些项目？",
            "介绍一下我自己",
            "黄志高的研究方向是什么？",
            "我的简历里有什么经历？",
            "本人的技术栈是什么？",
        ],
    )
    def test_self_reference_goes_personal(self, question: str) -> None:
        selection = _select(question)
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.PERSONAL_SELF_REFERENCE

    def test_self_reference_outranks_current_information(self) -> None:
        # Personal Facts Integrity: "我的项目最新版本" must never spend
        # Search API budget or fold web results into personal biography.
        selection = _select("我的项目最新版本是什么？")
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.PERSONAL_SELF_REFERENCE

    def test_product_knowledge_outranks_current_information(self) -> None:
        selection = _select("ZGLab Personal AI Agent 当前有哪些核心能力？")
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.PERSONAL_KNOWLEDGE_REFERENCE

    @pytest.mark.parametrize(
        "question",
        [
            "Python 最新版本是什么？",
            "某个开源项目最近有什么更新？",
            "HTTP/3 标准目前的状态如何？",
            "今天有什么技术新闻？",
            "What is the latest release of FastAPI?",
            "What is the current stable release of Python?",
        ],
    )
    def test_current_information_goes_web(self, question: str) -> None:
        selection = _select(question)
        assert selection.capability_id == WEB_RESEARCH_CAPABILITY_ID
        assert selection.reason == SelectionReason.CURRENT_INFORMATION

    @pytest.mark.parametrize(
        "question",
        [
            "什么是 RAG？",
            "解释一下向量检索的原理",
            "如何评估检索质量？",
        ],
    )
    def test_ambiguous_defaults_to_personal(self, question: str) -> None:
        # Conservative fallback: ambiguity never spends search budget.
        selection = _select(question)
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.DEFAULT_PERSONAL

    def test_auto_web_degrades_to_personal_while_disabled(self) -> None:
        selection = _select("Python 最新版本是什么？", enabled=False)
        assert selection.capability_id == PERSONAL_KNOWLEDGE_CAPABILITY_ID
        assert selection.reason == SelectionReason.WEB_DISABLED_FALLBACK_PERSONAL


class TestPolicyShape:
    def test_selection_is_a_single_code_not_a_reasoning_chain(self) -> None:
        selection = _select("什么是 RAG？")
        assert isinstance(selection.reason.value, str)
        # No free-form explanation fields exist on the result.
        assert {field for field in selection.__dataclass_fields__} == {
            "capability_id",
            "reason",
        }

    def test_unknown_mode_string_rejected_by_enum(self) -> None:
        with pytest.raises(ValueError):
            AskMode("planner")  # arbitrary capability ids never accepted
