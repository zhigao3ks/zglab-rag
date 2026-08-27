<script setup lang="ts">
import { computed, ref } from "vue";
import type { AskMode } from "../api/contracts";
import { QUESTION_MAX_LENGTH } from "../api/contracts";

const props = defineProps<{
  disabled: boolean;
}>();

const emit = defineEmits<{
  (event: "submit", question: string, mode: AskMode): void;
}>();

const draft = ref("");
// Phase 12D: lightweight, user-controlled capability mode. The server
// re-validates it; this control never represents authorization.
const mode = ref<AskMode>("auto");

const trimmed = computed(() => draft.value.trim());
const canSend = computed(
  () => !props.disabled && trimmed.value.length > 0 && trimmed.value.length <= QUESTION_MAX_LENGTH,
);
const overLimit = computed(() => draft.value.length > QUESTION_MAX_LENGTH);

function send(): void {
  if (!canSend.value) {
    return;
  }
  emit("submit", trimmed.value, mode.value);
  draft.value = "";
}

function onKeydown(event: KeyboardEvent): void {
  // Enter submits; Shift+Enter inserts a newline.
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
}
</script>

<template>
  <form class="composer" @submit.prevent="send">
    <label class="composer__label" for="question-input">向助手提问</label>
    <div class="composer__box" :class="{ 'composer__box--invalid': overLimit }">
      <textarea
        id="question-input"
        v-model="draft"
        class="composer__textarea"
        data-testid="question-input"
        rows="2"
        :maxlength="QUESTION_MAX_LENGTH + 200"
        placeholder="输入你的问题，Enter 发送，Shift + Enter 换行"
        aria-label="向助手提问"
        :disabled="disabled"
        @keydown="onKeydown"
      ></textarea>
      <div class="composer__bar">
        <span
          class="composer__count"
          :class="{ 'composer__count--over': overLimit }"
          aria-live="polite"
        >
          {{ draft.length }} / {{ QUESTION_MAX_LENGTH }}
        </span>
        <label class="composer__mode">
          <span class="composer__mode-label">回答方式</span>
          <select v-model="mode" class="composer__mode-select" data-testid="mode-select" :disabled="disabled">
            <option value="auto">自动</option>
            <option value="personal">个人知识库</option>
            <option value="web">联网检索</option>
          </select>
        </label>
        <button
          type="submit"
          class="composer__send"
          data-testid="send-button"
          :disabled="!canSend"
        >
          {{ disabled ? "回答中…" : "发送" }}
        </button>
      </div>
    </div>
  </form>
</template>

<style scoped>
.composer {
  padding: var(--space-3) 0 var(--space-5);
}

.composer__label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--font-size-small);
  color: var(--text-muted);
}

.composer__box {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  background: var(--surface-card);
  padding: var(--space-3);
  transition: border-color 0.15s ease;
}

.composer__box:focus-within {
  border-color: var(--accent);
}

.composer__box--invalid {
  border-color: var(--danger);
}

.composer__textarea {
  width: 100%;
  border: none;
  outline: none;
  resize: vertical;
  min-height: 3rem;
  font: inherit;
  color: var(--text-primary);
  background: transparent;
}

.composer__textarea::placeholder {
  color: var(--text-muted);
}

.composer__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--space-2);
}

.composer__count {
  font-size: var(--font-size-small);
  color: var(--text-muted);
}

.composer__count--over {
  color: var(--danger);
  font-weight: 600;
}

.composer__mode {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--font-size-small);
  color: var(--text-muted);
}

.composer__mode-select {
  font: inherit;
  font-size: var(--font-size-small);
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-small);
  padding: 2px 6px;
}

.composer__mode-select:disabled {
  opacity: 0.45;
}

.composer__send {
  font: inherit;
  border: none;
  border-radius: var(--radius-small);
  padding: var(--space-2) var(--space-5);
  background: var(--accent);
  color: var(--accent-contrast);
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.composer__send:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.composer__send:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
</style>
