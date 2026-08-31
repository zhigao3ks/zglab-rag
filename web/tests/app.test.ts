/**
 * Assistant view tests with a mocked askStream client: UI state machine,
 * conversation semantics, validation and lifecycle. No real backend.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import AssistantView from "../src/views/AssistantView.vue";
import type { AskStreamCallbacks } from "../src/api/client";
import type { PublicAskResponse } from "../src/api/contracts";
import { authState } from "../src/auth/store";

const { askStreamMock, conversationApi } = vi.hoisted(() => ({
  askStreamMock: vi.fn(),
  conversationApi: {
    listConversations: vi.fn(),
    createConversation: vi.fn(),
    deleteConversation: vi.fn(),
    listConversationMessages: vi.fn(),
  },
}));

vi.mock("../src/api/client", () => ({
  askStream: (...args: unknown[]) => askStreamMock(...args),
}));

// Phase 15A3: the assistant loads conversations on mount; default to an
// empty list so the pre-existing scenarios stay deterministic. Session
// behavior has dedicated coverage in session.test.ts.
vi.mock("../src/conversation/api", () => ({
  listConversations: (...args: unknown[]) => conversationApi.listConversations(...args),
  createConversation: (...args: unknown[]) => conversationApi.createConversation(...args),
  deleteConversation: (...args: unknown[]) => conversationApi.deleteConversation(...args),
  listConversationMessages: (...args: unknown[]) =>
    conversationApi.listConversationMessages(...args),
}));

/** Mount the authenticated assistant view with a stub router. */
function mountAssistant() {
  authState.user = { username: "alice", role: "USER" };
  authState.csrfToken = "test-csrf";
  authState.restoring = false;
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div />" } }],
  });
  return mount(AssistantView, { global: { plugins: [router] } });
}

function completedPayload(overrides: Partial<PublicAskResponse> = {}): PublicAskResponse {
  return {
    request_id: "r1",
    status: "answered",
    answer: "这是回答。",
    sources: [
      { id: "E1", title: "测试文档", section: ["第一章", "小节"], source_path: "test/a.md" },
    ],
    ...overrides,
  };
}

function mockHappyPath(payload: PublicAskResponse = completedPayload()) {
  askStreamMock.mockImplementation(async (_question: string, callbacks: AskStreamCallbacks) => {
    callbacks.onStage("accepted", payload.request_id);
    callbacks.onStage("retrieving", payload.request_id);
    callbacks.onStage("generating", payload.request_id);
    callbacks.onStage("validating", payload.request_id);
    callbacks.onCompleted(payload);
  });
}

function mockPending() {
  askStreamMock.mockImplementation(
    async (_question: string, callbacks: AskStreamCallbacks) => {
      callbacks.onStage("retrieving", "r1");
      await new Promise(() => {
        /* never resolves: simulates in-flight generation */
      });
    },
  );
}

function setScrollerMetrics(
  element: HTMLElement,
  { scrollHeight, clientHeight, scrollTop }: Record<string, number>,
): void {
  Object.defineProperties(element, {
    scrollHeight: { configurable: true, value: scrollHeight },
    clientHeight: { configurable: true, value: clientHeight },
    scrollTop: { configurable: true, writable: true, value: scrollTop },
  });
}

async function typeAndSubmit(wrapper: ReturnType<typeof mount>, question: string) {
  await wrapper.find('[data-testid="question-input"]').setValue(question);
  await wrapper.find('[data-testid="question-input"]').trigger("keydown", { key: "Enter" });
}

beforeEach(() => {
  askStreamMock.mockReset();
  conversationApi.listConversations.mockReset();
  conversationApi.listConversations.mockResolvedValue({ ok: true, data: [] });
  conversationApi.createConversation.mockReset();
  conversationApi.deleteConversation.mockReset();
  conversationApi.listConversationMessages.mockReset();
  conversationApi.listConversationMessages.mockResolvedValue({ ok: true, data: [] });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("empty state", () => {
  it("shows introduction and example prompts", () => {
    const wrapper = mountAssistant();
    const workspace = wrapper.find('[data-testid="assistant-workspace"]');
    expect(workspace.exists()).toBe(true);
    expect(workspace.find('[data-testid="message-scroller"]').exists()).toBe(true);
    const empty = wrapper.find('[data-testid="empty-state"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("ZGLab Personal Knowledge Assistant");
    const examples = wrapper.findAll(".conversation__example-button");
    expect(examples.length).toBeGreaterThanOrEqual(3);
    expect(examples[0].text()).toBe("你是谁？");
  });

  it("clicking an example prompt sends it directly", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await wrapper.findAll(".conversation__example-button")[0].trigger("click");
    await flushPromises();
    expect(askStreamMock).toHaveBeenCalledTimes(1);
    expect(askStreamMock.mock.calls[0][0]).toBe("你是谁？");
    expect(wrapper.find('[data-testid="answer-text"]').text()).toBe("这是回答。");
  });
});

describe("composer validation", () => {
  it("disables send for empty question", () => {
    const wrapper = mountAssistant();
    expect(wrapper.find('[data-testid="send-button"]').attributes("disabled")).toBeDefined();
  });

  it("rejects whitespace-only question", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "   ");
    expect(askStreamMock).not.toHaveBeenCalled();
  });

  it("prevents sending over 1000 characters", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "长".repeat(1001));
    expect(askStreamMock).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="send-button"]').attributes("disabled")).toBeDefined();
  });

  it("submits on Enter", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "你是谁？");
    expect(askStreamMock).toHaveBeenCalledTimes(1);
  });

  it("does not submit on Shift+Enter (newline)", async () => {
    const wrapper = mountAssistant();
    const input = wrapper.find('[data-testid="question-input"]');
    await input.setValue("第一行");
    await input.trigger("keydown", { key: "Enter", shiftKey: true });
    expect(askStreamMock).not.toHaveBeenCalled();
  });
});

describe("state machine", () => {
  it("shows retrieving / generating / validating labels while pending", async () => {
    const stages: Array<() => void> = [];
    let release: (value?: unknown) => void = () => {};
    const hold = new Promise((resolve) => {
      release = resolve;
    });
    askStreamMock.mockImplementation((_question: string, callbacks: AskStreamCallbacks) => {
      const push = (stage: "retrieving" | "generating" | "validating") =>
        stages.push(() => callbacks.onStage(stage, "r1"));
      push("retrieving");
      push("generating");
      push("validating");
      callbacks.onStage("retrieving", "r1");
      return hold;
    });

    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "问题？");
    await flushPromises();
    expect(wrapper.find('[data-testid="status-indicator"]').text()).toContain(
      "正在检索公开知识库",
    );

    stages[1](); // generating
    await flushPromises();
    expect(wrapper.find('[data-testid="status-indicator"]').text()).toContain("正在整理回答");

    stages[2](); // validating
    await flushPromises();
    expect(wrapper.find('[data-testid="status-indicator"]').text()).toContain("正在核验引用");

    release();
  });

  it("renders completed answered with answer and sources", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "问题？");
    await flushPromises();

    expect(wrapper.find('[data-testid="answer-text"]').text()).toBe("这是回答。");
    const source = wrapper.find('[data-testid="source-item"]');
    expect(source.exists()).toBe(true);
    expect(source.text()).toContain("测试文档");
    // Section breadcrumb rendering.
    expect(source.text()).toContain("第一章 › 小节");
    expect(source.text()).toContain("test/a.md");
  });

  it("renders insufficient_evidence as normal business result", async () => {
    mockHappyPath(
      completedPayload({
        status: "insufficient_evidence",
        answer: "当前公开知识库中没有足够信息回答这个问题。",
        sources: [],
      }),
    );
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "红烧肉怎么做？");
    await flushPromises();

    expect(wrapper.find('[data-testid="answer-text"]').text()).toContain(
      "当前公开知识库中没有足够信息回答这个问题。",
    );
    // Not a red system error, no copy button, no sources.
    expect(wrapper.find('[data-testid="error-card"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="copy-button"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="source-item"]').exists()).toBe(false);
  });

  it("renders SSE error with safe message and request id", async () => {
    askStreamMock.mockImplementation(async (_q: string, callbacks: AskStreamCallbacks) => {
      callbacks.onError("RATE_LIMITED", "r9");
    });
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "问题？");
    await flushPromises();

    const errorCard = wrapper.find('[data-testid="error-card"]');
    expect(errorCard.exists()).toBe(true);
    expect(errorCard.text()).toContain("请求有点频繁，请稍后再试。");
    expect(errorCard.text()).toContain("请求编号：r9");
  });

  it("prevents duplicate submit while a request is pending", async () => {
    mockPending();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "第一个问题？");
    await flushPromises();
    // Second submit attempt while pending is ignored.
    await wrapper.find('[data-testid="question-input"]').setValue("第二个问题？");
    await wrapper.find('[data-testid="question-input"]').trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(askStreamMock).toHaveBeenCalledTimes(1);
  });
});

describe("conversation scrolling", () => {
  it("forces following on submit and follows stage, completed, and error updates", async () => {
    let callbacks: AskStreamCallbacks | undefined;
    let release: () => void = () => {};
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    askStreamMock.mockImplementation((_question: string, received: AskStreamCallbacks) => {
      callbacks = received;
      return hold;
    });

    const wrapper = mountAssistant();
    const scroller = wrapper.find('[data-testid="message-scroller"]').element as HTMLElement;
    setScrollerMetrics(scroller, { scrollHeight: 800, clientHeight: 300, scrollTop: 0 });

    await typeAndSubmit(wrapper, "滚动测试？");
    await flushPromises();
    expect(scroller.scrollTop).toBe(800);

    setScrollerMetrics(scroller, { scrollHeight: 1000, clientHeight: 300, scrollTop: 800 });
    callbacks?.onStage("generating", "r1");
    await flushPromises();
    expect(scroller.scrollTop).toBe(1000);

    setScrollerMetrics(scroller, { scrollHeight: 1200, clientHeight: 300, scrollTop: 1000 });
    callbacks?.onCompleted(completedPayload());
    await flushPromises();
    expect(scroller.scrollTop).toBe(1200);

    setScrollerMetrics(scroller, { scrollHeight: 1400, clientHeight: 300, scrollTop: 1200 });
    callbacks?.onError("RATE_LIMITED", "r1");
    await flushPromises();
    expect(scroller.scrollTop).toBe(1400);
    release();
  });

  it("does not take scroll control from a detached reader for stage, completed, or error", async () => {
    let callbacks: AskStreamCallbacks | undefined;
    let release: () => void = () => {};
    const hold = new Promise<void>((resolve) => {
      release = resolve;
    });
    askStreamMock.mockImplementation((_question: string, received: AskStreamCallbacks) => {
      callbacks = received;
      return hold;
    });

    const wrapper = mountAssistant();
    const scroller = wrapper.find('[data-testid="message-scroller"]').element as HTMLElement;
    setScrollerMetrics(scroller, { scrollHeight: 1000, clientHeight: 300, scrollTop: 100 });
    await typeAndSubmit(wrapper, "不要抢滚动？");
    await flushPromises();

    scroller.scrollTop = 50;
    await wrapper.find('[data-testid="message-scroller"]').trigger("scroll");
    expect(wrapper.find('[data-testid="return-latest"]').exists()).toBe(true);

    callbacks?.onStage("generating", "r1");
    await flushPromises();
    expect(scroller.scrollTop).toBe(50);

    callbacks?.onCompleted(completedPayload());
    await flushPromises();
    expect(scroller.scrollTop).toBe(50);

    callbacks?.onError("RATE_LIMITED", "r1");
    await flushPromises();
    expect(scroller.scrollTop).toBe(50);
    release();
  });

  it("returns to the latest message when the detached control is clicked", async () => {
    const wrapper = mountAssistant();
    const messageScroller = () =>
      wrapper.find('[data-testid="message-scroller"]').element as HTMLElement;
    const scrollTo = vi.fn();

    setScrollerMetrics(messageScroller(), { scrollHeight: 1000, clientHeight: 300, scrollTop: 0 });
    await wrapper.find('[data-testid="message-scroller"]').trigger("scroll");
    expect(wrapper.find('[data-testid="return-latest"]').exists()).toBe(true);

    Object.defineProperty(messageScroller(), "scrollTo", {
      configurable: true,
      value: scrollTo,
    });
    await wrapper.find('[data-testid="return-latest"]').trigger("click");
    expect(scrollTo).toHaveBeenCalledWith({ top: 1000, behavior: "smooth" });
    expect(wrapper.find('[data-testid="return-latest"]').exists()).toBe(false);
  });
});

describe("conversation semantics", () => {
  it("shows conversation locally across multiple turns", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "第一个问题？");
    await flushPromises();
    mockHappyPath(completedPayload({ request_id: "r2", answer: "第二个回答。" }));
    await typeAndSubmit(wrapper, "第二个问题？");
    await flushPromises();

    const bubbles = wrapper.findAll(".conversation__bubble--user");
    expect(bubbles.length).toBe(2);
    expect(bubbles[0].text()).toBe("第一个问题？");
    expect(bubbles[1].text()).toBe("第二个问题？");
    expect(wrapper.text()).toContain("这是回答。");
    expect(wrapper.text()).toContain("第二个回答。");
  });

  it("never sends history with the next request", async () => {
    mockHappyPath();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "第一个问题？");
    await flushPromises();
    await typeAndSubmit(wrapper, "第二个问题？");
    await flushPromises();

    expect(askStreamMock.mock.calls[0][0]).toBe("第一个问题？");
    expect(askStreamMock.mock.calls[1][0]).toBe("第二个问题？");
  });

  it("aborts the in-flight request on unmount", async () => {
    mockPending();
    const wrapper = mountAssistant();
    await typeAndSubmit(wrapper, "问题？");
    await flushPromises();

    const signal = askStreamMock.mock.calls[0][2] as AbortSignal;
    expect(signal.aborted).toBe(false);
    wrapper.unmount();
    expect(signal.aborted).toBe(true);
  });
});
