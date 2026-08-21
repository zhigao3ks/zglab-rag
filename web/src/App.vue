<script setup lang="ts">
import { computed, onUnmounted, ref } from "vue";
import AssistantHeader from "./components/AssistantHeader.vue";
import ConversationView from "./components/ConversationView.vue";
import QuestionComposer from "./components/QuestionComposer.vue";
import { askStream } from "./api/client";
import type {
  FrontendErrorCode,
  PublicAskResponse,
  StreamStage,
} from "./api/contracts";
import { QUESTION_MAX_LENGTH } from "./api/contracts";

/**
 * One assistant turn. Every question is an independent request: history
 * is shown locally in memory only and is never sent to the backend.
 */
export type AssistantTurn =
  | { phase: "pending"; stage: StreamStage | null }
  | { phase: "completed"; completed: PublicAskResponse }
  | { phase: "error"; code: FrontendErrorCode; requestId: string | null };

export type ChatMessage =
  | { id: number; role: "user"; text: string }
  | { id: number; role: "assistant"; turn: AssistantTurn };

const messages = ref<ChatMessage[]>([]);
const pending = ref(false);

let nextId = 1;
let abortController: AbortController | null = null;

const hasConversation = computed(() => messages.value.length > 0);

function updateLastAssistantTurn(turn: AssistantTurn): void {
  for (let index = messages.value.length - 1; index >= 0; index -= 1) {
    const message = messages.value[index];
    if (message.role === "assistant") {
      message.turn = turn;
      return;
    }
  }
}

async function submit(rawQuestion: string): Promise<void> {
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
        },
        onError(code, requestId) {
          updateLastAssistantTurn({
            phase: "error",
            code,
            requestId: requestId || null,
          });
        },
        onNetworkFailure() {
          updateLastAssistantTurn({ phase: "error", code: "NETWORK", requestId: null });
        },
      },
      controller.signal,
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
  <div class="app-shell">
    <AssistantHeader />
    <main class="app-main">
      <ConversationView
        :messages="messages"
        :has-conversation="hasConversation"
        @example="submit"
      />
      <QuestionComposer :disabled="pending" @submit="submit" />
    </main>
    <footer class="app-footer">
      回答基于已允许公开的知识源生成，并附带可追溯的来源。
    </footer>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--surface-page);
}

.app-main {
  flex: 1;
  width: 100%;
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: 0 var(--space-4);
  display: flex;
  flex-direction: column;
}

.app-footer {
  padding: var(--space-4);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}
</style>
