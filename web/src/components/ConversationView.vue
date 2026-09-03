<script setup lang="ts">
import type { ChatMessage } from "../conversation/types";
import AnswerCard from "./AnswerCard.vue";

defineProps<{
  messages: ChatMessage[];
  hasConversation: boolean;
}>();

const emit = defineEmits<{
  (event: "example", question: string): void;
}>();

// Real questions the public knowledge base can stably answer.
const EXAMPLE_PROMPTS = [
  "ZGLab Personal AI Agent 当前有哪些核心能力？",
  "Agentic 项目中的 Agent workflow 包含哪些主要能力？",
  "多 Agent 医疗系统的 Agent 架构是什么？",
  "企业 AI 会议助手如何处理实时录音、转写和断线恢复？",
];
</script>

<template>
  <section class="conversation" aria-label="对话记录">
    <div v-if="!hasConversation" class="conversation__empty" data-testid="empty-state">
      <h2 class="conversation__empty-title">ZGLab Personal AI Agent</h2>
      <p class="conversation__empty-intro">
        我会基于公开、可追溯的知识源回答问题，并给出来源。试着问我：
      </p>
      <ul class="conversation__examples">
        <li v-for="prompt in EXAMPLE_PROMPTS" :key="prompt">
          <button
            type="button"
            class="conversation__example-button"
            @click="emit('example', prompt)"
          >
            {{ prompt }}
          </button>
        </li>
      </ul>
    </div>

    <ul v-else class="conversation__list">
      <template v-for="message in messages" :key="message.id">
        <li v-if="message.role === 'user'" class="conversation__row conversation__row--user">
          <div class="conversation__bubble conversation__bubble--user">
            {{ message.text }}
          </div>
        </li>
        <li v-else class="conversation__row conversation__row--assistant">
          <AnswerCard :turn="message.turn" />
        </li>
      </template>
    </ul>
  </section>
</template>

<style scoped>
.conversation {
  min-height: 100%;
  padding: var(--space-6) 0 var(--space-4);
}

.conversation__empty {
  max-width: 640px;
  margin: 8vh auto 0;
  text-align: center;
}

.conversation__empty-title {
  margin: 0;
  font-size: var(--font-size-heading);
  font-weight: 600;
  color: var(--text-primary);
}

.conversation__empty-intro {
  margin: var(--space-3) 0 0;
  color: var(--text-secondary);
}

.conversation__examples {
  list-style: none;
  margin: var(--space-5) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  align-items: center;
}

.conversation__example-button {
  font: inherit;
  color: var(--text-primary);
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  padding: var(--space-2) var(--space-4);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.conversation__example-button:hover {
  border-color: var(--accent);
  background: var(--surface-hover);
}

.conversation__example-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.conversation__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.conversation__row {
  display: flex;
}

.conversation__row--user {
  justify-content: flex-end;
}

.conversation__row--assistant {
  justify-content: flex-start;
}

.conversation__bubble {
  max-width: min(85%, 640px);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-medium);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.conversation__bubble--user {
  background: var(--accent-soft);
  border: 1px solid var(--accent-border);
  color: var(--text-primary);
}

@media (max-width: 768px) {
  .conversation {
    padding-top: var(--space-4);
  }

  .conversation__empty {
    margin-top: var(--space-5);
    padding: 0 var(--space-2);
  }

  .conversation__bubble {
    max-width: 92%;
  }
}
</style>
