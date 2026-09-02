<script setup lang="ts">
import { router } from "../router";
import { isAuthenticated } from "../auth/store";

const capabilities = [
  { title: "Personal Knowledge", text: "检索公开项目、技术笔记与知识卡，并返回可追溯的来源。" },
  { title: "Evidence Grounding", text: "回答只使用可验证的证据；信息不足时会明确说明。" },
  { title: "Citation Validation", text: "生成后核验引用，避免把没有来源的推测当作事实。" },
  { title: "Web Research", text: "按需检索公开网页，保留来源链接并与个人知识区分展示。" },
  { title: "MCP Tools", text: "通过受控的确定性工具完成适合工具处理的任务。" },
  { title: "Agent Orchestration", text: "在明确授权和边界内组合知识、联网研究与工具能力。" },
];

function goLogin(): void {
  router.push({ name: "login" });
}
</script>

<template>
  <div class="landing">
    <header class="landing__header">
      <div class="landing__header-inner">
        <p class="landing__brand">ZGLab</p>
        <button
          v-if="isAuthenticated()"
          type="button"
          class="landing__login-button"
          data-testid="landing-enter"
          @click="router.push({ name: 'assistant' })"
        >
          进入助手
        </button>
        <button
          v-else
          type="button"
          class="landing__login-button"
          data-testid="landing-login"
          @click="goLogin"
        >
          登录
        </button>
      </div>
    </header>

    <main class="landing__main">
      <h1 class="landing__title">ZGLab AI Assistant</h1>
      <p class="landing__subtitle">
        基于公开、可追溯知识源的 AI 工作台。登录后可使用个人知识、联网研究、受控工具与 Agent 协作能力。
      </p>

      <section class="landing__grid" data-testid="capability-grid">
        <article
          v-for="capability in capabilities"
          :key="capability.title"
          class="landing__card"
        >
          <div class="landing__card-head">
            <h2 class="landing__card-title">{{ capability.title }}</h2>
            <span class="landing__badge landing__badge--live">
              已上线
            </span>
          </div>
          <p class="landing__card-text">{{ capability.text }}</p>
        </article>
      </section>

      <section class="landing__links">
        <a class="landing__link" href="https://github.com/zhigao3ks" target="_blank" rel="noopener">GitHub</a>
        <a class="landing__link" href="https://zglab.fun" target="_blank" rel="noopener">个人主页</a>
        <a class="landing__link" href="/login" data-testid="landing-login-link">登录</a>
      </section>
    </main>

    <footer class="landing__footer">
      产品介绍匿名可见；问答等消费型 AI 能力需要登录后使用。
    </footer>
  </div>
</template>

<style scoped>
.landing {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
  background: var(--surface-page);
}

.landing__header {
  border-bottom: 1px solid var(--border-subtle);
}

.landing__header-inner {
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: var(--space-4);
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.landing__brand {
  margin: 0;
  font-size: var(--font-size-small);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.landing__login-button {
  border: 1px solid var(--accent-border);
  background: var(--accent);
  color: var(--accent-contrast);
  border-radius: var(--radius-small);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-body);
  cursor: pointer;
}

.landing__main {
  flex: 1;
  width: 100%;
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: var(--space-6) var(--space-4);
}

.landing__title {
  margin: 0;
  font-size: var(--font-size-heading);
  font-weight: 600;
}

.landing__subtitle {
  margin: var(--space-3) 0 0;
  color: var(--text-secondary);
}

.landing__grid {
  margin-top: var(--space-6);
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: var(--space-4);
}

.landing__card {
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  padding: var(--space-4);
}

.landing__card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.landing__card-title {
  margin: 0;
  font-size: var(--font-size-body);
  font-weight: 600;
}

.landing__badge {
  font-size: var(--font-size-small);
  border-radius: var(--radius-small);
  padding: 0 var(--space-2);
}

.landing__badge--live {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--accent-border);
}

.landing__card-text {
  margin: var(--space-2) 0 0;
  color: var(--text-secondary);
  font-size: var(--font-size-small);
}

.landing__links {
  margin-top: var(--space-6);
  display: flex;
  gap: var(--space-4);
}

.landing__link {
  color: var(--accent);
  text-decoration: none;
  font-size: var(--font-size-body);
}

.landing__footer {
  padding: var(--space-4);
  text-align: center;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}
</style>
