<script setup lang="ts">
import { ref } from "vue";
import type { ConversationPayload } from "../conversation/api";

defineProps<{
  conversations: ConversationPayload[];
  activeConversationId: number | null;
  /** Mobile drawer state; the sidebar is always visible on desktop. */
  open: boolean;
}>();

const emit = defineEmits<{
  (event: "create"): void;
  (event: "select", conversationId: number): void;
  (event: "remove", conversationId: number): void;
}>();

// Two-step inline confirm so a single stray click cannot delete a session.
const pendingDeleteId = ref<number | null>(null);

function requestRemove(conversationId: number): void {
  pendingDeleteId.value = conversationId;
}

function confirmRemove(): void {
  if (pendingDeleteId.value === null) {
    return;
  }
  emit("remove", pendingDeleteId.value);
  pendingDeleteId.value = null;
}

function cancelRemove(): void {
  pendingDeleteId.value = null;
}

function select(conversationId: number): void {
  pendingDeleteId.value = null;
  emit("select", conversationId);
}

function create(): void {
  pendingDeleteId.value = null;
  emit("create");
}
</script>

<template>
  <aside
    class="session-sidebar"
    :class="{ 'session-sidebar--open': open }"
    data-testid="session-sidebar"
    aria-label="历史会话"
  >
    <button
      type="button"
      class="session-sidebar__new"
      data-testid="new-conversation"
      @click="create"
    >
      新建会话
    </button>

    <ul v-if="conversations.length > 0" class="session-sidebar__list">
      <li
        v-for="conversation in conversations"
        :key="conversation.id"
        class="session-sidebar__entry"
      >
        <button
          type="button"
          class="session-sidebar__item"
          :class="{ 'session-sidebar__item--active': conversation.id === activeConversationId }"
          :data-conversation-id="conversation.id"
          data-testid="conversation-item"
          @click="select(conversation.id)"
        >
          <span class="session-sidebar__title">{{ conversation.title }}</span>
        </button>
        <template v-if="pendingDeleteId === conversation.id">
          <button
            type="button"
            class="session-sidebar__confirm"
            data-testid="confirm-delete-conversation"
            @click="confirmRemove"
          >
            确认删除
          </button>
          <button
            type="button"
            class="session-sidebar__cancel"
            data-testid="cancel-delete-conversation"
            @click="cancelRemove"
          >
            取消
          </button>
        </template>
        <button
          v-else
          type="button"
          class="session-sidebar__delete"
          :data-conversation-id="conversation.id"
          data-testid="delete-conversation"
          :aria-label="`删除会话：${conversation.title}`"
          @click="requestRemove(conversation.id)"
        >
          删除
        </button>
      </li>
    </ul>
    <p v-else class="session-sidebar__empty" data-testid="sidebar-empty">暂无历史会话</p>
  </aside>
</template>

<style scoped>
.session-sidebar {
  flex: none;
  width: 260px;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-3);
  background: var(--surface-card);
  border-right: 1px solid var(--border-subtle);
  overflow-y: auto;
}

.session-sidebar__new {
  flex: none;
  border: 1px solid var(--accent-border);
  background: var(--accent);
  color: var(--accent-contrast);
  border-radius: var(--radius-small);
  padding: var(--space-2) var(--space-3);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.session-sidebar__new:hover {
  filter: brightness(1.05);
}

.session-sidebar__new:focus-visible,
.session-sidebar__item:focus-visible,
.session-sidebar__delete:focus-visible,
.session-sidebar__confirm:focus-visible,
.session-sidebar__cancel:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.session-sidebar__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.session-sidebar__entry {
  display: flex;
  align-items: center;
  gap: var(--space-1);
}

.session-sidebar__item {
  flex: 1;
  min-width: 0;
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary);
  border-radius: var(--radius-small);
  padding: var(--space-2);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.session-sidebar__item:hover {
  background: var(--surface-hover);
  color: var(--text-primary);
}

.session-sidebar__item--active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--text-primary);
}

.session-sidebar__title {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-sidebar__delete,
.session-sidebar__cancel {
  flex: none;
  border: none;
  background: transparent;
  color: var(--text-muted);
  border-radius: var(--radius-small);
  padding: var(--space-1) var(--space-2);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.session-sidebar__delete:hover {
  color: var(--danger);
}

.session-sidebar__confirm {
  flex: none;
  border: 1px solid var(--danger);
  background: transparent;
  color: var(--danger);
  border-radius: var(--radius-small);
  padding: var(--space-1) var(--space-2);
  font-size: var(--font-size-small);
  cursor: pointer;
}

.session-sidebar__empty {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

@media (max-width: 768px) {
  .session-sidebar {
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    z-index: 30;
    width: min(280px, 82vw);
    transform: translateX(-100%);
    visibility: hidden;
    transition:
      transform 0.2s ease,
      visibility 0.2s;
    box-shadow: 0 0 24px rgb(15 20 28 / 25%);
  }

  .session-sidebar--open {
    transform: translateX(0);
    visibility: visible;
  }
}
</style>
