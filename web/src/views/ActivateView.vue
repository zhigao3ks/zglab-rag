<script setup lang="ts">
import { computed, ref } from "vue";
import { authActivate, authResetPassword } from "../auth/api";
import type { PublicErrorCode } from "../api/contracts";

const MIN_PASSWORD_LENGTH = 12;

/**
 * Read the one-time credential from the URL FRAGMENT (#token=...).
 *
 * Fragments never reach the server, so the token stays out of Nginx
 * access logs, application logs and Referer headers. The hash is wiped
 * from the address bar / history immediately with history.replaceState,
 * and the token is afterwards only ever sent in a POST body.
 */
function consumeCredentialHash(): { token: string; purpose: "activate" | "reset" } {
  const raw = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
  const params = new URLSearchParams(raw);
  const token = params.get("token") ?? "";
  const purpose = params.get("purpose") === "reset" ? "reset" : "activate";
  if (raw) {
    window.history.replaceState(
      null,
      "",
      window.location.pathname + window.location.search,
    );
  }
  return { token, purpose };
}

const credential = consumeCredentialHash();
const token = credential.token;
const purpose = credential.purpose;
const missingToken = token.length === 0;

const password = ref("");
const confirmation = ref("");
const submitting = ref(false);
const done = ref(false);
const failure = ref<PublicErrorCode | "NETWORK" | "MISMATCH" | null>(null);

const failureLabel = computed(() => {
  switch (failure.value) {
    case "MISMATCH":
      return "两次输入的密码不一致。";
    case "INVALID_REQUEST":
      return "链接无效或已过期，也可能密码不满足长度要求（至少 12 位）。";
    case "ACCOUNT_UNAVAILABLE":
      return "账号当前不可用，请联系管理员。";
    case "RATE_LIMITED":
      return "请求过于频繁，请稍后再试。";
    default:
      return "操作失败，请稍后重试。";
  }
});

async function submit(): Promise<void> {
  if (submitting.value || done.value || missingToken) {
    return;
  }
  if (password.value.length < MIN_PASSWORD_LENGTH) {
    failure.value = "INVALID_REQUEST";
    return;
  }
  if (password.value !== confirmation.value) {
    failure.value = "MISMATCH";
    return;
  }
  submitting.value = true;
  failure.value = null;
  try {
    // Purpose-pinned endpoints: activation tokens and reset tokens are
    // consumed by different server routes and never cross boundaries.
    const call = purpose === "reset" ? authResetPassword : authActivate;
    const result = await call(token, password.value);
    if (result.ok) {
      done.value = true;
    } else {
      failure.value = result.code;
    }
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="activate-page">
    <div v-if="missingToken" class="activate-card" data-testid="activate-missing">
      <h1 class="activate-card__title">链接不完整</h1>
      <p class="activate-card__hint">
        未检测到一次性凭据，请使用管理员下发的完整链接（含 #token=… 部分）。
      </p>
      <router-link class="activate-card__login" to="/">← 返回首页</router-link>
    </div>

    <form
      v-else-if="!done"
      class="activate-card"
      data-testid="activate-form"
      @submit.prevent="submit"
    >
      <p class="activate-card__brand">ZGLab</p>
      <h1 class="activate-card__title">设置密码</h1>
      <p class="activate-card__hint">
        使用管理员下发的一次性链接为账号设置密码。链接只能使用一次。
      </p>

      <label class="activate-card__label">
        新密码（至少 {{ MIN_PASSWORD_LENGTH }} 位）
        <input
          v-model="password"
          type="password"
          autocomplete="new-password"
          class="activate-card__input"
          data-testid="activate-password"
        />
      </label>

      <label class="activate-card__label">
        确认新密码
        <input
          v-model="confirmation"
          type="password"
          autocomplete="new-password"
          class="activate-card__input"
          data-testid="activate-confirmation"
        />
      </label>

      <p
        v-if="failure !== null"
        class="activate-card__error"
        role="alert"
        data-testid="activate-error"
      >
        {{ failureLabel }}
      </p>

      <button
        type="submit"
        class="activate-card__submit"
        :disabled="submitting"
        data-testid="activate-submit"
      >
        {{ submitting ? "正在设置…" : "设置密码" }}
      </button>
    </form>

    <div v-else class="activate-card" data-testid="activate-done">
      <h1 class="activate-card__title">设置成功</h1>
      <p class="activate-card__hint">密码已生效，旧会话（如有）已被撤销。</p>
      <router-link class="activate-card__login" to="/login" data-testid="activate-goto-login">
        前往登录
      </router-link>
    </div>
  </div>
</template>

<style scoped>
.activate-page {
  min-height: 100dvh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-4);
  background: var(--surface-page);
}

.activate-card {
  width: 100%;
  max-width: 400px;
  background: var(--surface-card);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-medium);
  padding: var(--space-6) var(--space-5);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.activate-card__brand {
  margin: 0;
  font-size: var(--font-size-small);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.activate-card__title {
  margin: 0;
  font-size: var(--font-size-title);
  font-weight: 600;
}

.activate-card__hint {
  margin: 0;
  color: var(--text-muted);
  font-size: var(--font-size-small);
}

.activate-card__label {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  color: var(--text-secondary);
  font-size: var(--font-size-small);
}

.activate-card__input {
  font-family: inherit;
  font-size: var(--font-size-body);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-small);
  background: var(--surface-page);
  color: var(--text-primary);
}

.activate-card__error {
  margin: 0;
  color: var(--danger);
  font-size: var(--font-size-small);
}

.activate-card__submit {
  margin-top: var(--space-2);
  border: 1px solid var(--accent-border);
  background: var(--accent);
  color: var(--accent-contrast);
  border-radius: var(--radius-small);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-body);
  cursor: pointer;
}

.activate-card__submit:disabled {
  opacity: 0.6;
  cursor: default;
}

.activate-card__login {
  color: var(--accent);
  text-decoration: none;
}
</style>
