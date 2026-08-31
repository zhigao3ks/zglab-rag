/**
 * Phase 15A3 session sidebar tests: conversation list, create, switch with
 * history restore, ask bound to the active conversation_id, history never
 * re-sent, delete flows, NOT_FOUND fallback and the mobile drawer toggle.
 * Both askStream and the conversation REST client are mocked; no backend.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import AssistantView from "../src/views/AssistantView.vue";
import SessionSidebar from "../src/components/SessionSidebar.vue";
import type { AskStreamCallbacks } from "../src/api/client";
import type { PublicAskResponse } from "../src/api/contracts";
import type {
  ConversationMessagePayload,
  ConversationPayload,
} from "../src/conversation/api";
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

vi.mock("../src/conversation/api", () => ({
  listConversations: (...args: unknown[]) => conversationApi.listConversations(...args),
  createConversation: (...args: unknown[]) => conversationApi.createConversation(...args),
  deleteConversation: (...args: unknown[]) => conversationApi.deleteConversation(...args),
  listConversationMessages: (...args: unknown[]) =>
    conversationApi.listConversationMessages(...args),
}));

function conversation(id: number, title: string): ConversationPayload {
  return {
    id,
    title,
    created_at: "2026-08-31T09:00:00Z",
    updated_at: "2026-08-31T10:00:00Z",
  };
}

function message(
  id: number,
  conversationId: number,
  role: "USER" | "ASSISTANT",
  content: string,
): ConversationMessagePayload {
  return {
    id,
    conversation_id: conversationId,
    role,
    content,
    created_at: "2026-08-31T10:00:00Z",
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function completedPayload(): PublicAskResponse {
  return {
    request_id: "r1",
    status: "answered",
    answer: "这是新回答。",
    sources: [],
  };
}

function mockHappyAsk(): void {
  askStreamMock.mockImplementation(async (_question: string, callbacks: AskStreamCallbacks) => {
    callbacks.onStage("accepted", "r1");
    callbacks.onCompleted(completedPayload());
  });
}

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

async function typeAndSubmit(wrapper: ReturnType<typeof mount>, question: string) {
  await wrapper.find('[data-testid="question-input"]').setValue(question);
  await wrapper.find('[data-testid="question-input"]').trigger("keydown", { key: "Enter" });
}

function items(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('[data-testid="conversation-item"]');
}

async function deleteRow(wrapper: ReturnType<typeof mount>, index: number) {
  await wrapper.findAll('[data-testid="delete-conversation"]')[index].trigger("click");
  await flushPromises();
  await wrapper.find('[data-testid="confirm-delete-conversation"]').trigger("click");
  await flushPromises();
}

beforeEach(() => {
  askStreamMock.mockReset();
  conversationApi.listConversations.mockReset().mockResolvedValue({ ok: true, data: [] });
  conversationApi.createConversation.mockReset();
  conversationApi.deleteConversation.mockReset();
  conversationApi.listConversationMessages.mockReset().mockResolvedValue({
    ok: true,
    data: [],
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SessionSidebar component", () => {
  it("renders backend order, marks the active conversation and emits events", async () => {
    const wrapper = mount(SessionSidebar, {
      props: {
        conversations: [conversation(2, "会话 B"), conversation(1, "会话 A")],
        activeConversationId: 1,
        open: false,
      },
    });

    const rows = wrapper.findAll('[data-testid="conversation-item"]');
    expect(rows.map((row) => row.text())).toEqual(["会话 B", "会话 A"]);
    expect(rows[0].classes()).not.toContain("session-sidebar__item--active");
    expect(rows[1].classes()).toContain("session-sidebar__item--active");

    await wrapper.find('[data-testid="new-conversation"]').trigger("click");
    expect(wrapper.emitted("create")).toHaveLength(1);

    await rows[0].trigger("click");
    expect(wrapper.emitted("select")).toEqual([[2]]);
  });

  it("requires a second confirming click before emitting remove", async () => {
    const wrapper = mount(SessionSidebar, {
      props: {
        conversations: [conversation(5, "要删的会话")],
        activeConversationId: null,
        open: false,
      },
    });

    const deleteButtons = wrapper.findAll('[data-testid="delete-conversation"]');
    await deleteButtons[0].trigger("click");
    expect(wrapper.emitted("remove")).toBeUndefined();
    expect(wrapper.find('[data-testid="confirm-delete-conversation"]').exists()).toBe(true);

    await wrapper.find('[data-testid="cancel-delete-conversation"]').trigger("click");
    expect(wrapper.find('[data-testid="confirm-delete-conversation"]').exists()).toBe(false);
    expect(wrapper.emitted("remove")).toBeUndefined();

    await wrapper.findAll('[data-testid="delete-conversation"]')[0].trigger("click");
    await wrapper.find('[data-testid="confirm-delete-conversation"]').trigger("click");
    expect(wrapper.emitted("remove")).toEqual([[5]]);
  });
});

describe("conversation list and history restore", () => {
  it("loads the list in backend order and restores the most recent conversation", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "Roadmap 问题"), conversation(1, "旧会话")],
    });
    conversationApi.listConversationMessages.mockImplementation(async (id: number) => {
      if (id === 2) {
        return {
          ok: true,
          data: [
            message(10, 2, "USER", "早上好，介绍一下 RAG 项目"),
            message(11, 2, "ASSISTANT", "这是历史回答。"),
          ],
        };
      }
      return { ok: true, data: [] };
    });

    const wrapper = mountAssistant();
    await flushPromises();

    const rows = items(wrapper);
    expect(rows.map((row) => row.text())).toEqual(["Roadmap 问题", "旧会话"]);
    expect(rows[0].classes()).toContain("session-sidebar__item--active");
    expect(conversationApi.listConversationMessages).toHaveBeenCalledWith(2);
    expect(wrapper.find(".conversation__bubble--user").text()).toBe(
      "早上好，介绍一下 RAG 项目",
    );
    expect(wrapper.find('[data-testid="answer-text"]').text()).toBe("这是历史回答。");
  });

  it("keeps the safe empty state when no conversations exist", async () => {
    const wrapper = mountAssistant();
    await flushPromises();

    expect(wrapper.find('[data-testid="sidebar-empty"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="conversation-item"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);
    expect(conversationApi.listConversationMessages).not.toHaveBeenCalled();
  });

  it("switches conversations and restores the selected history", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockImplementation(async (id: number) => ({
      ok: true,
      data:
        id === 1
          ? [message(1, 1, "USER", "会话 A 的问题")]
          : [message(2, 2, "USER", "会话 B 的问题")],
    }));

    const wrapper = mountAssistant();
    await flushPromises();
    expect(wrapper.find(".conversation__bubble--user").text()).toBe("会话 B 的问题");

    await items(wrapper)[1].trigger("click");
    await flushPromises();

    expect(conversationApi.listConversationMessages).toHaveBeenLastCalledWith(1);
    expect(wrapper.find(".conversation__bubble--user").text()).toBe("会话 A 的问题");
    expect(items(wrapper)[1].classes()).toContain("session-sidebar__item--active");
    expect(items(wrapper)[0].classes()).not.toContain("session-sidebar__item--active");
  });

  it("does not re-send restored history with the next ask", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B")],
    });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: true,
      data: [
        message(1, 2, "USER", "历史问题"),
        message(2, 2, "ASSISTANT", "历史回答"),
      ],
    });
    mockHappyAsk();

    const wrapper = mountAssistant();
    await flushPromises();
    await typeAndSubmit(wrapper, "新问题？");
    await flushPromises();

    // Only the current question travels to ask; the 5th argument is the
    // persistence-only conversation_id. History never appears anywhere.
    expect(askStreamMock).toHaveBeenCalledTimes(1);
    expect(askStreamMock.mock.calls[0][0]).toBe("新问题？");
    expect(askStreamMock.mock.calls[0][4]).toBe(2);
    const answers = wrapper.findAll('[data-testid="answer-text"]');
    expect(answers[answers.length - 1].text()).toBe("这是新回答。");
  });
});

describe("create conversation", () => {
  it("creates, activates and binds the next ask to the new conversation", async () => {
    conversationApi.listConversations
      .mockResolvedValueOnce({ ok: true, data: [] })
      .mockResolvedValue({ ok: true, data: [conversation(7, "新对话")] });
    conversationApi.createConversation.mockResolvedValue({
      ok: true,
      data: conversation(7, "新对话"),
    });
    mockHappyAsk();

    const wrapper = mountAssistant();
    await flushPromises();

    await wrapper.find('[data-testid="new-conversation"]').trigger("click");
    await flushPromises();

    expect(conversationApi.createConversation).toHaveBeenCalledWith("test-csrf", "新对话");
    const rows = items(wrapper);
    expect(rows.map((row) => row.text())).toEqual(["新对话"]);
    expect(rows[0].classes()).toContain("session-sidebar__item--active");
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);

    await typeAndSubmit(wrapper, "第一个持久化问题？");
    await flushPromises();

    expect(askStreamMock.mock.calls[0][4]).toBe(7);
  });

  it("does not auto-create a conversation when asking without one", async () => {
    mockHappyAsk();
    const wrapper = mountAssistant();
    await flushPromises();

    await typeAndSubmit(wrapper, "没有会话的问题？");
    await flushPromises();

    expect(conversationApi.createConversation).not.toHaveBeenCalled();
    expect(askStreamMock.mock.calls[0][4]).toBeNull();
  });
});

describe("delete conversation", () => {
  it("removes a non-active conversation and keeps the active one", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: true,
      data: [message(1, 2, "USER", "会话 B 的问题")],
    });
    conversationApi.deleteConversation.mockResolvedValue({ ok: true, data: null });

    const wrapper = mountAssistant();
    await flushPromises();

    await deleteRow(wrapper, 1);
    await flushPromises();

    expect(conversationApi.deleteConversation).toHaveBeenCalledWith("test-csrf", 1);
    expect(items(wrapper).map((row) => row.text())).toEqual(["会话 B"]);
    expect(items(wrapper)[0].classes()).toContain("session-sidebar__item--active");
    expect(wrapper.find(".conversation__bubble--user").text()).toBe("会话 B 的问题");
  });

  it("clears local state when the active conversation is deleted", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: true,
      data: [message(1, 2, "USER", "会话 B 的问题")],
    });
    conversationApi.deleteConversation.mockResolvedValue({ ok: true, data: null });

    const wrapper = mountAssistant();
    await flushPromises();

    await deleteRow(wrapper, 0);
    await flushPromises();

    expect(items(wrapper).map((row) => row.text())).toEqual(["会话 A"]);
    expect(items(wrapper)[0].classes()).not.toContain("session-sidebar__item--active");
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="conversation-notice"]').exists()).toBe(false);
  });
});

describe("NOT_FOUND safety", () => {
  it("falls back to the safe empty state when restore returns NOT_FOUND", async () => {
    conversationApi.listConversations
      .mockResolvedValueOnce({ ok: true, data: [conversation(2, "已删除的会话")] })
      .mockResolvedValue({ ok: true, data: [] });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: false,
      code: "NOT_FOUND",
      requestId: "r5",
    });

    const wrapper = mountAssistant();
    await flushPromises();

    expect(wrapper.find('[data-testid="conversation-notice"]').text()).toContain(
      "不存在或已被删除",
    );
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="conversation-item"]').exists()).toBe(false);
    expect(conversationApi.listConversations).toHaveBeenCalledTimes(2);
  });

  it("falls back to the safe empty state when an ask returns NOT_FOUND", async () => {
    conversationApi.listConversations
      .mockResolvedValueOnce({ ok: true, data: [conversation(2, "会话 B")] })
      .mockResolvedValue({ ok: true, data: [] });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: true,
      data: [message(1, 2, "USER", "历史问题")],
    });
    askStreamMock.mockImplementation(async (_q: string, callbacks: AskStreamCallbacks) => {
      callbacks.onError("NOT_FOUND", "r9");
    });

    const wrapper = mountAssistant();
    await flushPromises();
    await typeAndSubmit(wrapper, "新问题？");
    await flushPromises();

    expect(wrapper.find('[data-testid="conversation-notice"]').text()).toContain(
      "不存在或已被删除",
    );
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="conversation-item"]').exists()).toBe(false);
  });
});

describe("mobile sidebar drawer", () => {
  it("toggles the sidebar and closes it from the backdrop", async () => {
    const wrapper = mountAssistant();
    await flushPromises();

    const sidebar = wrapper.find('[data-testid="session-sidebar"]');
    expect(sidebar.classes()).not.toContain("session-sidebar--open");
    expect(wrapper.find('[data-testid="sidebar-backdrop"]').exists()).toBe(false);

    await wrapper.find('[data-testid="sidebar-toggle"]').trigger("click");
    expect(wrapper.find('[data-testid="session-sidebar"]').classes()).toContain(
      "session-sidebar--open",
    );
    expect(wrapper.find('[data-testid="sidebar-backdrop"]').exists()).toBe(true);

    await wrapper.find('[data-testid="sidebar-backdrop"]').trigger("click");
    expect(wrapper.find('[data-testid="session-sidebar"]').classes()).not.toContain(
      "session-sidebar--open",
    );
    expect(wrapper.find('[data-testid="sidebar-backdrop"]').exists()).toBe(false);
  });

  it("closes the drawer after selecting a conversation", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    const wrapper = mountAssistant();
    await flushPromises();

    await wrapper.find('[data-testid="sidebar-toggle"]').trigger("click");
    await items(wrapper)[1].trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-testid="session-sidebar"]').classes()).not.toContain(
      "session-sidebar--open",
    );
  });
});

describe("pending ask guards", () => {
  it("ignores switching while a request is pending", async () => {
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockResolvedValue({
      ok: true,
      data: [message(1, 2, "USER", "会话 B 的问题")],
    });
    askStreamMock.mockImplementation(
      async (_question: string, _callbacks: AskStreamCallbacks) => {
        await new Promise(() => {
          /* never resolves: in-flight generation */
        });
      },
    );

    const wrapper = mountAssistant();
    await flushPromises();
    await typeAndSubmit(wrapper, "进行中的问题？");
    await flushPromises();

    await items(wrapper)[1].trigger("click");
    await flushPromises();

    expect(conversationApi.listConversationMessages).not.toHaveBeenCalledWith(1);
    expect(wrapper.find(".conversation__bubble--user").text()).toBe("会话 B 的问题");
  });
});

describe("stale conversation response fencing", () => {
  it("does not let an older history restore replace a newly created active conversation", async () => {
    const restoreA = deferred<{
      ok: true;
      data: ConversationMessagePayload[];
    }>();
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockImplementation(() => restoreA.promise);
    conversationApi.createConversation.mockResolvedValue({
      ok: true,
      data: conversation(2, "新对话"),
    });

    const wrapper = mountAssistant();
    await flushPromises();
    expect(conversationApi.listConversationMessages).toHaveBeenCalledWith(1);

    await wrapper.find('[data-testid="new-conversation"]').trigger("click");
    await flushPromises();
    expect(items(wrapper)[0].classes()).toContain("session-sidebar__item--active");
    expect(items(wrapper)[0].text()).toContain("新对话");

    restoreA.resolve({ ok: true, data: [message(1, 1, "USER", "旧会话消息")] });
    await flushPromises();

    expect(items(wrapper)[0].classes()).toContain("session-sidebar__item--active");
    expect(wrapper.find('[data-testid="empty-state"]').exists()).toBe(true);
    expect(wrapper.text()).not.toContain("旧会话消息");
  });

  it("does not let an older refresh restore a deleted conversation row", async () => {
    const staleList = deferred<{ ok: true; data: ConversationPayload[] }>();
    conversationApi.listConversations
      .mockResolvedValueOnce({ ok: true, data: [conversation(1, "会话 A")] })
      .mockImplementationOnce(() => staleList.promise);
    conversationApi.listConversationMessages.mockResolvedValue({ ok: true, data: [] });
    conversationApi.deleteConversation.mockResolvedValue({ ok: true, data: null });
    mockHappyAsk();

    const wrapper = mountAssistant();
    await flushPromises();
    await typeAndSubmit(wrapper, "触发刷新");
    await flushPromises();
    expect(conversationApi.listConversations).toHaveBeenCalledTimes(2);

    await deleteRow(wrapper, 0);
    expect(items(wrapper)).toHaveLength(0);

    staleList.resolve({ ok: true, data: [conversation(1, "会话 A")] });
    await flushPromises();
    expect(items(wrapper)).toHaveLength(0);
  });

  it("keeps the newer selection when an older restore finishes last", async () => {
    const restoreA = deferred<{
      ok: true;
      data: ConversationMessagePayload[];
    }>();
    conversationApi.listConversations.mockResolvedValue({
      ok: true,
      data: [conversation(3, "会话 C"), conversation(2, "会话 B"), conversation(1, "会话 A")],
    });
    conversationApi.listConversationMessages.mockImplementation((id: number) => {
      if (id === 1) {
        return restoreA.promise;
      }
      return Promise.resolve({ ok: true, data: [message(id, id, "USER", `会话 ${id}`)] });
    });

    const wrapper = mountAssistant();
    await flushPromises();
    await items(wrapper)[2].trigger("click");
    await items(wrapper)[1].trigger("click");
    await flushPromises();

    expect(items(wrapper)[1].classes()).toContain("session-sidebar__item--active");
    expect(wrapper.text()).toContain("会话 2");

    restoreA.resolve({ ok: true, data: [message(1, 1, "USER", "过期的会话 A")] });
    await flushPromises();
    expect(items(wrapper)[1].classes()).toContain("session-sidebar__item--active");
    expect(wrapper.text()).toContain("会话 2");
    expect(wrapper.text()).not.toContain("过期的会话 A");
  });

  it("ignores pending list results after unmount", async () => {
    const staleList = deferred<{ ok: true; data: ConversationPayload[] }>();
    conversationApi.listConversations.mockImplementation(() => staleList.promise);

    const wrapper = mountAssistant();
    wrapper.unmount();
    staleList.resolve({ ok: true, data: [conversation(1, "不应恢复")] });
    await flushPromises();

    expect(conversationApi.listConversationMessages).not.toHaveBeenCalled();
  });
});
