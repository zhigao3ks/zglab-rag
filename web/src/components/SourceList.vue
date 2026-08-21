<script setup lang="ts">
import type { PublicSource } from "../api/contracts";

defineProps<{
  sources: PublicSource[];
}>();
</script>

<template>
  <section class="source-list" aria-label="回答来源">
    <h3 class="source-list__title">Sources</h3>
    <ol class="source-list__items">
      <li
        v-for="(source, index) in sources"
        :key="source.id"
        class="source-list__item"
        data-testid="source-item"
      >
        <span class="source-list__index">[{{ index + 1 }}]</span>
        <div class="source-list__body">
          <p class="source-list__source-title">{{ source.title }}</p>
          <p v-if="source.section.length > 0" class="source-list__breadcrumb">
            {{ source.section.join(" › ") }}
          </p>
          <p class="source-list__path">{{ source.source_path }}</p>
        </div>
      </li>
    </ol>
  </section>
</template>

<style scoped>
.source-list {
  border-top: 1px solid var(--border-subtle);
  padding-top: var(--space-3);
}

.source-list__title {
  margin: 0 0 var(--space-2);
  font-size: var(--font-size-small);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.source-list__items {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.source-list__item {
  display: flex;
  gap: var(--space-2);
}

.source-list__index {
  flex: none;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

.source-list__body {
  min-width: 0;
}

.source-list__source-title {
  margin: 0;
  color: var(--text-primary);
  font-weight: 500;
  overflow-wrap: anywhere;
}

.source-list__breadcrumb {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-small);
  color: var(--text-secondary);
  overflow-wrap: anywhere;
}

.source-list__path {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-small);
  color: var(--text-muted);
  overflow-wrap: anywhere;
}
</style>
