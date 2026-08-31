<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, type ComponentPublicInstance } from "vue";
import { useRouter } from "vue-router";
import AssistantHeader from "../components/AssistantHeader.vue";
import ConversationView from "../components/ConversationView.vue";
import QuestionComposer from "../components/QuestionComposer.vue";
import SessionSidebar from "../components/SessionSidebar.vue";
import { askStream } from "../api/client";
import { ERROR_LABELS, type AskMode } from "../api/contracts";
import { QUESTION_MAX_LENGTH } from "../api/contracts";
import type { AssistantTurn, ChatMessage } from "../conversation/types";
import {
  createConversation as createConversationApi,
  deleteConversation as deleteConversationApi,
  listConversationMessages,
  listConversations,
  type ConversationMessagePayload,
  type ConversationPayload,
} from "../conversation/api";
import { authState, clearAuth, logout } from "../auth/store";
import { authChangePassword } from "../auth/api";
import { useConversationScroll } from "../conversation/useConversationScroll";

const router = useRouter();

// One assistant turn: every question is an independent request; history
// is shown locally in memory only and is never sent to the backend.

const messages = ref<ChatMessage[]>([]);
const pending = ref(false);

let nextId = 1;
let abortController: AbortController | null = null;

const hasConversation = computed(() => messages.value.length > 0);
const { scroller, isDetached, onScroll, followAfterUpdate, followNow } = useConversationScroll();

function setMessageScroller(element: Element | ComponentPublicInstance | null): void {
  scroller.value = element instanceof HTMLElement ? element : null;
}

// -- session sidebar (Phase 15A3) -------------------------------------------
// Session history is persisted UI history only. Restored messages are
// display-only: they never re-enter ask, retrieval, prompt or the agent.

const DEFAULT_CONVERSATION_TITLE = "新对话";

const conversations = ref<ConversationPayload[]>([]);
const activeConversationId = ref<number | null>(null);
const sidebarOpen = ref(false);
const conversationNotice = ref<string | null>(null);
let conversationSwitchSeq = 0;

async function handleAuthLost(): Promise<void> {
  clearAuth();
  await router.push({ name: "login" });
}

async function refreshConversations(): Promise<boolean> {
  const result = await listConversations();
  if (result.ok) {
    conversations.value = result.data;
    return true;
  }
  if (result.code === "AUTHENTICATION_REQUIRED") {
    await handleAuthLost();
    return false;
  }
  conversationNotice.value = ERROR_LABELS[result.code];
  return false;
}

function mapRestoredMessages(payloads: ConversationMessagePayload[]): ChatMessage[] {
  return payloads.map((payload, index) => {
    if (payload.role === "USER") {
      return { id: index + 1, role: "user" as const, text: payload.content };
    }
    // Restored assistant turns are display-only; sources and request ids
    // are not persisted, so history renders as plain answer text.
    return {
      id: index + 1,
      role: "assistant" as const,
      turn: {
        phase: "completed" as const,
        completed: {
          request_id: "",
          status: "answered" as const,
          answer: payload.content,
          sources: [],
        },
      },
    };
  });
}

async function selectConversation(conversationId: number): Promise<void> {
  // A pending ask still writes into the local view; switching now would
  // corrupt whichever history is on screen. Server persistence is separate.
  if (pending.value || conversationId === activeConversationId.value) {
    return;
  }
  const switchToken = ++conversationSwitchSeq;
  conversationNotice.value = null;
  const result = await listConversationMessages(conversationId);
  if (switchToken !== conversationSwitchSeq) {
    return; // A newer selection superseded this one.
  }
  if (!result.ok) {
    if (result.code === "AUTHENTICATION_REQUIRED") {
      await handleAuthLost();
      return;
    }
    if (result.code === "NOT_FOUND") {
      await handleConversationMissing();
      return;
    }
    conversationNotice.value = ERROR_LABELS[result.code];
    return;
  }
  activeConversationId.value = conversationId;
  messages.value = mapRestoredMessages(result.data);
  nextId = messages.value.length + 1;
  sidebarOpen.value = false;
  followNow();
}

// The active conversation disappeared server-side (deleted here or from
// another session, both share the same 404). Never surface the raw server
// message: clear local state and fall back to the safe empty view.
async function handleConversationMissing(): Promise<void> {
  conversationNotice.value = ERROR_LABELS.NOT_FOUND;
  activeConversationId.value = null;
  messages.value = [];
  nextId = 1;
  await refreshConversations();
}

async function createConversation(): Promise<void> {
  if (pending.value) {
    return;
  }
  conversationNotice.value = null;
  const result = await createConversationApi(authState.csrfToken, DEFAULT_CONVERSATION_TITLE);
  if (!result.ok) {
    if (result.code === "AUTHENTICATION_REQUIRED") {
      await handleAuthLost();
      return;
    }
    conversationNotice.value = ERROR_LABELS[result.code];
    return;
  }
  // New conversation is the most recently updated; prepend in backend order.
  conversations.value = [result.data, ...conversations.value];
  activeConversationId.value = result.data.id;
  messages.value = [];
  nextId = 1;
  sidebarOpen.value = false;
}

async function removeConversation(conversationId: number): Promise<void> {
  if (pending.value) {
    return;
  }
  conversationNotice.value = null;
  const result = await deleteConversationApi(authState.csrfToken, conversationId);
  if (result.ok === false && result.code === "AUTHENTICATION_REQUIRED") {
    await handleAuthLost();
    return;
  }
  if (result.ok === false && result.code !== "NOT_FOUND") {
    conversationNotice.value = ERROR_LABELS[result.code];
    return;
  }
  if (result.ok === false) {
    // Already gone server-side; treat the delete as done and clean up.
    conversationNotice.value = ERROR_LABELS.NOT_FOUND;
  }
  conversations.value = conversations.value.filter(
    (conversation) => conversation.id !== conversationId,
  );
  if (activeConversationId.value === conversationId) {
    activeConversationId.value = null;
    messages.value = [];
    nextId = 1;
  }
}

// Enter the assistant: load the list, then default to the most recent
// conversation (backend order is updated_at DESC) and restore it. With no
// history, stay in the empty state until the user creates a conversation.
onMounted(() => {
  void (async () => {
    if (await refreshConversations()) {
      const latest = conversations.value[0];
      if (latest) {
        await selectConversation(latest.id);
      }
    }
  })();
});

// -- account bar (logout / change password) -------------------------------

const showPasswordForm = ref(false);
const currentPassword = ref("");
const newPassword = ref("");
const passwordMessage = ref<string | null>(null);
const passwordError = ref<string | null>(null);
const passwordBusy = ref(false);

async function doLogout(): Promise<void> {
  await logout();
  await router.push({ name: "login" });
}

async function submitPasswordChange(): Promise<void> {
  if (passwordBusy.value) {
    return;
  }
  passwordBusy.value = true;
  passwordMessage.value = null;
  passwordError.value = null;
  try {
    const result = await authChangePassword(
      authState.csrfToken,
      currentPassword.value,
      newPassword.value,
    );
    if (result.ok) {
      passwordMessage.value = "密码已更新，其他会话已被撤销。";
      currentPassword.value = "";
      newPassword.value = "";
    } else if (result.code === "INVALID_CREDENTIALS") {
      passwordError.value = "当前密码不正确。";
    } else if (result.code === "INVALID_REQUEST") {
      passwordError.value = "新密码不满足策略（至少 12 位）。";
    } else if (result.code === "AUTHENTICATION_REQUIRED") {
      clearAuth();
      await router.push({ name: "login" });
    } else {
      passwordError.value = "修改失败，请稍后重试。";
    }
  } finally {
    passwordBusy.value = false;
  }
}

// -- assistant turns ---------------------------------------------------------

function updateLastAssistantTurn(turn: AssistantTurn): void {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    const message = messages.value[index];
    if (message.role === "assistant") {
      message.turn = turn;
      void followAfterUpdate();
      return;
    }
  }
}

async function submit(rawQuestion: string, mode: AskMode = "auto"): Promise<void> {
  const question = rawQuestion.trim();
  if (pending.value || question.length === 0 || question.length > QUESTION_MAX_LENGTH) {
    return;
  }

  messages.value.push({ id: nextId++, role: "user", text: question });
  messages.value.push({
    id: nextId++,
    role: "assistant",
    turn: { phase: "pending", stage: null },
  });
  followNow();
  void followAfterUpdate();
  pending.value = true;

  const controller = new AbortController();
  abortController = controller;
  try {
    await askStream(
      question,
      {
        onStage(stage) {
          updateLastAssistantTurn({ phase: "pending", stage });
        },
        onCompleted(completed) {
          updateLastAssistantTurn({ phase: "completed", completed });
          // Keep sidebar order in sync with the persisted updated_at.
          if (activeConversationId.value !== null) {
            void refreshConversations();
          }
        },
        async onError(code, requestId) {
          updateLastAssistantTurn({
            phase: "error",
            code,
            requestId: requestId || null,
          });
          // Session lost mid-use (admin revoke / expiry): drop local
          // state and return to login. Server authorization stays the
          // source of truth.
          if (code === "AUTHENTICATION_REQUIRED") {
            clearAuth();
            await router.push({ name: "login" });
          } else if (code === "NOT_FOUND" && activeConversationId.value !== null) {
            // The bound conversation was deleted while this ask ran.
            await handleConversationMissing();
          }
        },
        onNetworkFailure() {
          updateLastAssistantTurn({ phase: "error", code: "NETWORK", requestId: null });
        },
      },
      controller.signal,
      mode,
      activeConversationId.value,
    );
  } finally {
    pending.value = false;
    if (abortController === controller) {
      abortController = null;
    }
  }
}

// Abort the in-flight fetch when leaving the page. Note: HTTP disconnect
// is NOT a backend generation cancellation guarantee.
onUnmounted(() => {
  abortController?.abort();
  abortController = null;
});
</script>

<template>
  <div class="app-shell assistant-layout">
    <SessionSidebar
      :conversations="conversations"
      :active-conversation-id="activeConversationId"
      :open="sidebarOpen"
      @create="createConversation"
      @select="selectConversation"
      @remove="removeConversation"
    />
    <div
      v-if="sidebarOpen"
      class="sidebar-backdrop"
      data-testid="sidebar-backdrop"
      @click="sidebarOpen = false"
    ></div>
    <div class="assistant-workspace" data-testid="assistant-workspace">
      <div class="account-bar" data-testid="account-bar">
        <button
          type="button"
          class="account-bar__action sidebar-toggle"
          data-testid="sidebar-toggle"
          @click="sidebarOpen = !sidebarOpen"
        >
          会话
        </button>
      <span class="account-bar__user" data-testid="account-username">
        {{ authState.user?.username }}
      </span>
      <button
        type="button"
        class="account-bar__action"
        data-testid="toggle-password-form"
        @click="showPasswordForm = !showPasswordForm"
      >
        修改密码
      </button>
      <button
        type="button"
        class="account-bar__action"
        data-testid="logout-button"
        @click="doLogout"
      >
        退出登录
      </button>
    </div>

    <form
      v-if="showPasswordForm"
      class="password-form"
      data-testid="password-form"
      @submit.prevent="submitPasswordChange"
    >
      <input
        v-model="currentPassword"
        type="password"
        autocomplete="current-password"
        placeholder="当前密码"
        class="password-form__input"
        data-testid="current-password"
      />
      <input
        v-model="newPassword"
        type="password"
        autocomplete="new-password"
        placeholder="新密码（至少 12 位）"
        class="password-form__input"
        data-testid="new-password"
      />
      <button type="submit" class="password-form__submit" :disabled="passwordBusy">
        提交
      </button>
      <p v-if="passwordMessage" class="password-form__ok" data-testid="password-ok">
        {{ passwordMessage }}
      </p>
      <p v-if="passwordError" class="password-form__error" data-testid="password-error">
        {{ passwordError }}
      </p>
    </form>

    <p v-if="conversationNotice" class="conversation-notice" data-testid="conversation-notice">
      {{ conversationNotice }}
    </p>

    <AssistantHeader />
    <main class="app-main">
      <div class="chat-area">
        <div
          :ref="setMessageScroller"
          class="message-scroller"
          data-testid="message-scroller"
          @scroll="onScroll"
        >
          <ConversationView
            :messages="messages"
            :has-conversation="hasConversation"
            @example="submit"
          />
        </div>
        <button
          v-if="isDetached"
          type="button"
          class="return-latest"
          data-testid="return-latest"
          @click="followNow(true)"
        >
          回到最新消息
        </button>
      </div>
      <div class="composer-dock">
        <QuestionComposer :disabled="pending" @submit="submit" />
      </div>
    </main>
      <footer class="app-footer">
        回答基于已允许公开的知识源生成，并附带可追溯的来源。
      </footer>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  height: 100dvh;
  overflow: hidden;
  display: flex;
  background: var(--surface-page);
}

.assistant-workspace {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.account-bar {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-3);
  max-width: var(--content-max-width);
  width: 100%;
  margin: 0 auto;
  padding: var(--space-2) var(--space-4) 0;
}

.account-bar__user {
  margin-right: auto;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

.account-bar__action {
  border: 1px solid var(--border-subtle);
  background: transparent;
  color: var(--text-secondary);
  border-radius: var(--radius-small);
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.password-form {
  flex: none;
  max-width: var(--content-max-width);
  width: 100%;
  margin: 0 auto;
  padding: var(--space-2) var(--space-4);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}

.password-form__input {
  font-family: inherit;
  font-size: var(--font-size-small);
  padding: var(--space-1) var(--space-2);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-small);
  background: var(--surface-card);
  color: var(--text-primary);
}

.password-form__submit {
  border: 1px solid var(--accent-border);
  background: var(--accent);
  color: var(--accent-contrast);
  border-radius: var(--radius-small);
  padding: var(--space-1) var(--space-3);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.password-form__ok {
  margin: 0;
  width: 100%;
  color: var(--accent);
  font-size: var(--font-size-small);
}

.password-form__error {
  margin: 0;
  width: 100%;
  color: var(--danger);
  font-size: var(--font-size-small);
}

.sidebar-toggle {
  display: none;
}

.conversation-notice {
  flex: none;
  max-width: var(--content-max-width);
  width: 100%;
  margin: 0 auto;
  padding: 0 var(--space-4) var(--space-1);
  color: var(--warning);
  font-size: var(--font-size-small);
}

.sidebar-backdrop {
  display: none;
}

.app-main {
  flex: 1;
  min-height: 0;
  width: 100%;
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: 0 var(--space-4);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-area {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.message-scroller {
  height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.composer-dock {
  flex: none;
  background: var(--surface-page);
  border-top: 1px solid var(--border-subtle);
}

.return-latest {
  position: absolute;
  right: var(--space-4);
  bottom: var(--space-3);
  border: 1px solid var(--accent-border);
  border-radius: 999px;
  background: var(--surface-card);
  color: var(--accent);
  padding: var(--space-2) var(--space-3);
  box-shadow: 0 2px 12px rgb(28 36 48 / 15%);
  cursor: pointer;
}

.app-footer {
  flex: none;
  padding: var(--space-4);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

@media (max-width: 768px) {
  .sidebar-toggle {
    display: inline-block;
  }

  .sidebar-backdrop {
    display: block;
    position: fixed;
    inset: 0;
    z-index: 20;
    background: rgb(15 20 28 / 45%);
  }

  .account-bar {
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  .account-bar__user {
    flex-basis: 100%;
  }

  .app-footer {
    display: none;
  }
}
</style>
