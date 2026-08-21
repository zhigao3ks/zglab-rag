<script setup lang="ts">
import { ref } from "vue";
import type { AssistantTurn } from "../App.vue";
import { ERROR_LABELS } from "../api/contracts";
import SourceList from "./SourceList.vue";
import StatusIndicator from "./StatusIndicator.vue";

const props = defineProps<{
  turn: AssistantTurn;
}>();

const copyState = ref<"idle" | "copied" | "failed">("idle");
let copyResetTimer: ReturnType<typeof setTimeout> | null = null;

async function copyAnswer(): Promise<void> {
  if (props.turn.phase !== "completed") {
    return;
  }
  try {
    // Copy only the answer text; never request_id, paths or diagnostics.
    await navigator.clipboard.writeText(props.turn.completed.answer);
    copyState.value = "copied";
  } catch {
    copyState.value = "failed";
  }
  if (copyResetTimer !== null) {
    clearTimeout(copyResetTimer);
  }
  copyResetTimer = setTimeout(() => {
    copyState.value = "idle";
    copyResetTimer = null;
  }, 2000);
}
</script>

<template>
  <div class="answer-card">
    <template v-if="turn.phase === 'pending'">
      <StatusIndicator :stage="turn.stage" />
    </template>

    <template v-else-if="turn.phase === 'completed'">
      <div
        class="answer-card__text"
        :class="{ 'answer-card__text--insufficient': turn.completed.status === 'insufficient_evidence' }"
        data-testid="answer-text"
      >
        {{ turn.completed.answer }}
      </div>

      <div v-if="turn.completed.status === 'answered'" class="answer-card__actions">
        <button
          type="button"
          class="answer-card__copy"
          data-testid="copy-button"
          @click="copyAnswer"
        >
          {{ copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制回答" }}
        </button>
      </div>

      <SourceList
        v-if="turn.completed.sources.length > 0"
        :sources="turn.completed.sources"
      />
    </template>

    <template v-else>
      <div class="answer-card__error" role="alert" data-testid="error-card">
        <p class="answer-card__error-text">{{ ERROR_LABELS[turn.code] }}</p>
        <p v-if="turn.requestId" class="answer-card__error-id">
          请求编号：{{ turn.requestId }}
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.answer-card {
  max-width: min(92%, 720px);
  width: 100%;
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.answer-card__text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  line-height: 1.7;
}

.answer-card__text--insufficient {
  color: var(--text-secondary);
}

.answer-card__actions {
  display: flex;
  gap: var(--space-2);
}

.answer-card__copy {
  font: inherit;
  font-size: var(--font-size-small);
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-small);
  padding: var(--space-1) var(--space-3);
  cursor: pointer;
}

.answer-card__copy:hover {
  border-color: var(--accent);
  color: var(--text-primary);
}

.answer-card__copy:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.answer-card__error {
  border-left: 3px solid var(--warning);
  padding-left: var(--space-3);
}

.answer-card__error-text {
  margin: 0;
  color: var(--text-primary);
}

.answer-card__error-id {
  margin: var(--space-2) 0 0;
  font-size: var(--font-size-small);
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
</style>
