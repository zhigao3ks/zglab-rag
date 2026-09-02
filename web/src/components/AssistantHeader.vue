<script setup lang="ts">
</script>

<template>
  <header class="assistant-header">
    <div class="assistant-header__inner">
      <p class="assistant-header__brand">ZGLab</p>
      <h1 class="assistant-header__title">ZGLab AI Assistant</h1>
      <p class="assistant-header__tagline">
        基于可追溯公开知识与受控能力，为项目、技术与研究问题提供回答。
      </p>
    </div>
  </header>
</template>

<style scoped>
.assistant-header {
  flex: none;
  border-bottom: 1px solid var(--border-subtle);
  background: var(--surface-page);
}

.assistant-header__inner {
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: var(--space-6) var(--space-4) var(--space-5);
}

.assistant-header__brand {
  margin: 0;
  font-size: var(--font-size-small);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.assistant-header__title {
  margin: var(--space-1) 0 0;
  font-size: var(--font-size-title);
  font-weight: 600;
  color: var(--text-primary);
}

.assistant-header__tagline {
  margin: var(--space-2) 0 0;
  color: var(--text-secondary);
  font-size: var(--font-size-body);
}
</style>
