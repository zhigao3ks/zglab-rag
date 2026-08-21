/**
 * Presentational component unit tests: AnswerCard (copy / XSS-safe text /
 * insufficient / error), SourceList and StatusIndicator.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import AnswerCard from "../src/components/AnswerCard.vue";
import SourceList from "../src/components/SourceList.vue";
import StatusIndicator from "../src/components/StatusIndicator.vue";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AnswerCard", () => {
  it("renders pending stage through the status indicator", () => {
    const wrapper = mount(AnswerCard, {
      props: { turn: { phase: "pending", stage: "generating" } },
    });
    expect(wrapper.find('[data-testid="status-indicator"]').text()).toContain("正在整理回答");
  });

  it("renders the answer as escaped text, never HTML (no v-html)", () => {
    const malicious = '<img src=x onerror=alert(1)><script>evil()</script>';
    const wrapper = mount(AnswerCard, {
      props: {
        turn: {
          phase: "completed",
          completed: {
            request_id: "r1",
            status: "answered",
            answer: malicious,
            sources: [],
          },
        },
      },
    });
    expect(wrapper.find("img").exists()).toBe(false);
    expect(wrapper.find("script").exists()).toBe(false);
    expect(wrapper.find('[data-testid="answer-text"]').text()).toContain(malicious);
  });

  it("copies only the answer text", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });

    const wrapper = mount(AnswerCard, {
      props: {
        turn: {
          phase: "completed",
          completed: {
            request_id: "r1",
            status: "answered",
            answer: "纯回答文本",
            sources: [{ id: "E1", title: "T", section: [], source_path: "a.md" }],
          },
        },
      },
    });
    await wrapper.find('[data-testid="copy-button"]').trigger("click");
    await flushPromises();

    expect(writeText).toHaveBeenCalledWith("纯回答文本");
    expect(wrapper.find('[data-testid="copy-button"]').text()).toBe("已复制");
  });

  it("shows a non-blocking hint when clipboard fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    Object.assign(navigator, { clipboard: { writeText } });

    const wrapper = mount(AnswerCard, {
      props: {
        turn: {
          phase: "completed",
          completed: { request_id: "r1", status: "answered", answer: "回答", sources: [] },
        },
      },
    });
    await wrapper.find('[data-testid="copy-button"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="copy-button"]').text()).toBe("复制失败");
  });

  it("renders error card with mapped message, not raw server text", () => {
    const wrapper = mount(AnswerCard, {
      props: {
        turn: { phase: "error", code: "GENERATION_TIMEOUT", requestId: "r7" },
      },
    });
    const card = wrapper.find('[data-testid="error-card"]');
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("本次回答耗时过长，可以稍后重试。");
    expect(card.text()).toContain("请求编号：r7");
    expect(card.attributes("role")).toBe("alert");
  });

  it("renders network failure with the safe network message", () => {
    const wrapper = mount(AnswerCard, {
      props: { turn: { phase: "error", code: "NETWORK", requestId: null } },
    });
    expect(wrapper.find('[data-testid="error-card"]').text()).toContain(
      "网络连接异常，请稍后重试。",
    );
  });
});

describe("SourceList", () => {
  it("renders numbered title, breadcrumb and secondary path", () => {
    const wrapper = mount(SourceList, {
      props: {
        sources: [
          {
            id: "E1",
            title: "Agent 长期记忆设计",
            section: ["设计", "核心架构"],
            source_path: "knowledge/agent-memory.md",
          },
        ],
      },
    });
    const item = wrapper.find('[data-testid="source-item"]');
    expect(item.text()).toContain("[1]");
    expect(item.text()).toContain("Agent 长期记忆设计");
    expect(item.text()).toContain("设计 › 核心架构");
    expect(item.text()).toContain("knowledge/agent-memory.md");
  });

  it("wraps long paths without breaking layout assumptions", () => {
    const longPath = "knowledge/" + "very-long-segment/".repeat(20) + "doc.md";
    const wrapper = mount(SourceList, {
      props: {
        sources: [{ id: "E1", title: "标题", section: [], source_path: longPath }],
      },
    });
    expect(wrapper.find(".source-list__path").text()).toBe(longPath);
  });
});

describe("StatusIndicator", () => {
  it("shows the accepted label before any backend stage", () => {
    const wrapper = mount(StatusIndicator, { props: { stage: null } });
    expect(wrapper.text()).toContain("已接收问题");
    expect(wrapper.attributes("aria-live")).toBe("polite");
  });

  it("maps every stage to visitor-facing Chinese labels", () => {
    const cases = [
      ["retrieving", "正在检索公开知识库"],
      ["generating", "正在整理回答"],
      ["validating", "正在核验引用"],
    ] as const;
    for (const [stage, label] of cases) {
      const wrapper = mount(StatusIndicator, { props: { stage } });
      expect(wrapper.text()).toContain(label);
    }
  });
});
