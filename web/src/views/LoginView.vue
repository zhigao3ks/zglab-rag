<script setup lang="ts">
import { ref } from "vue";
import { useRouter } from "vue-router";
import { login } from "../auth/store";
import { ERROR_LABELS, type FrontendErrorCode } from "../api/contracts";

const router = useRouter();
const username = ref("");
const password = ref("");
const submitting = ref(false);
const errorCode = ref<FrontendErrorCode | null>(null);

async function submit(): Promise<void> {
  if (submitting.value || username.value.trim().length === 0 || password.value.length === 0) {
    return;
  }
  submitting.value = true;
  errorCode.value = null;
  try {
    const outcome = await login(username.value.trim(), password.value);
    if (outcome.ok) {
      await router.push({ name: "assistant" });
    } else {
      errorCode.value = outcome.code;
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="login-page">
    <form class="login-card" data-testid="login-form" @submit.prevent="submit">
      <p class="login-card__brand">ZGLab</p>
      <h1 class="login-card__title">登录</h1>
      <p class="login-card__hint">
        账号由管理员创建并下发；本系统不提供公开注册。
      </p>

      <label class="login-card__label">
        用户名
        <input
          v-model="username"
          type="text"
          autocomplete="username"
          class="login-card__input"
          data-testid="login-username"
        />
      </label>

      <label class="login-card__label">
        密码
        <input
          v-model="password"
          type="password"
          autocomplete="current-password"
          class="login-card__input"
          data-testid="login-password"
        />
      </label>

      <p
        v-if="errorCode !== null"
        class="login-card__error"
        role="alert"
        data-testid="login-error"
      >
        {{ ERROR_LABELS[errorCode] }}
      </p>

      <button
        type="submit"
        class="login-card__submit"
        :disabled="submitting"
        data-testid="login-submit"
      >
        {{ submitting ? "正在登录…" : "登录" }}
      </button>

      <router-link class="login-card__back" to="/">← 返回首页</router-link>
    </form>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background: var(--surface-page);
}

.login-card {
  width: 100%;
  max-width: 380px;
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  padding: var(--space-6) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.login-card__brand {
  margin: 0;
  font-size: var(--font-size-small);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.login-card__title {
  margin: 0;
  font-size: var(--font-size-title);
  font-weight: 600;
}

.login-card__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

.login-card__label {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  color: var(--text-secondary);
  font-size: var(--font-size-small);
}

.login-card__input {
  font-family: inherit;
  font-size: var(--font-size-body);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-small);
  background: var(--surface-page);
  color: var(--text-primary);
}

.login-card__error {
  margin: 0;
  color: var(--danger);
  font-size: var(--font-size-small);
}

.login-card__submit {
  margin-top: var(--space-2);
  border: 1px solid var(--accent-border);
  background: var(--accent);
  color: var(--accent-contrast);
  border-radius: var(--radius-small);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-body);
  cursor: pointer;
}

.login-card__submit:disabled {
  opacity: 0.6;
  cursor: default;
}

.login-card__back {
  color: var(--text-muted);
  font-size: var(--font-size-small);
  text-decoration: none;
}
</style>
