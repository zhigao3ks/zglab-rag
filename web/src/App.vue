<script setup lang="ts">
import { onMounted } from "vue";
import { authState, restoreSession } from "./auth/store";

/**
 * Application shell (Phase 11): restores the session from the HttpOnly
 * cookie via GET /api/v2/auth/me before routing, so route guards and the
 * landing/assistant split see a deterministic auth state. No session
 * token, password or JWT is ever stored client-side.
 */
onMounted(() => {
  void restoreSession();
});
</script>

<template>
  <div v-if="authState.restoring" class="app-restoring" data-testid="app-restoring">
    正在加载…
  </div>
  <router-view v-else />
</template>

<style scoped>
.app-restoring {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: var(--font-size-body);
  background: var(--surface-page);
}
</style>
