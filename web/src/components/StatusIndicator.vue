<script setup lang="ts">
import { computed } from "vue";
import type { StreamStage } from "../api/contracts";
import { STAGE_LABELS } from "../api/contracts";

const props = defineProps<{
  stage: StreamStage | null;
}>();

// Heartbeats never change the visible stage; only real backend stages do.
const label = computed(() =>
  props.stage === null ? "已接收问题…" : STAGE_LABELS[props.stage],
);
</script>

<template>
  <div class="status-indicator" role="status" aria-live="polite" data-testid="status-indicator">
    <span class="status-indicator__dots" aria-hidden="true">
      <span class="status-indicator__dot"></span>
      <span class="status-indicator__dot"></span>
      <span class="status-indicator__dot"></span>
    </span>
    <span class="status-indicator__label">{{ label }}</span>
  </div>
</template>

<style scoped>
.status-indicator {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--text-secondary);
}

.status-indicator__dots {
  display: inline-flex;
  gap: 4px;
}

.status-indicator__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: status-pulse 1.2s infinite ease-in-out;
}

.status-indicator__dot:nth-child(2) {
  animation-delay: 0.2s;
}

.status-indicator__dot:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes status-pulse {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-indicator__dot {
    animation: none;
    opacity: 0.6;
  }
}
</style>
