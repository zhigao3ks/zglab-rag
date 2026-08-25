/**
 * Phase 11 frontend auth tests: public landing, login, activation,
 * auth-state restore/logout and the UX-only route guard. fetch is
 * mocked; no real backend is touched.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter, type Router } from "vue-router";
import LandingView from "../src/views/LandingView.vue";
import LoginView from "../src/views/LoginView.vue";
import ActivateView from "../src/views/ActivateView.vue";
import { authState, clearAuth, login, restoreSession } from "../src/auth/store";
import { router as appRouter } from "../src/router";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SESSION_PAYLOAD = {
  request_id: "r1",
  user: { username: "alice", role: "USER" },
  csrf_token: "csrf-123",
};

function resetAuthState(): void {
  authState.user = null;
  authState.csrfToken = null;
  authState.restoring = true;
}

async function makeRouter(): Promise<Router> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: appRouter.getRoutes().map((route) => ({
      path: route.path,
      name: route.name,
      component: route.components?.default ?? { template: "<div />" },
      meta: route.meta,
      redirect: route.redirect as never,
    })),
  });
  // Re-create guards equivalent to the app router's.
  router.beforeEach((to) => {
    if (to.meta.requiresAuth && authState.user === null) {
      return { name: "login" };
    }
    return true;
  });
  await router.push("/");
  await router.isReady();
  return router;
}

beforeEach(() => {
  resetAuthState();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  resetAuthState();
});

describe("landing (public, anonymous)", () => {
  it("shows capability cards and a login entry", async () => {
    const router = await makeRouter();
    const wrapper = mount(LandingView, { global: { plugins: [router] } });
    const grid = wrapper.find('[data-testid="capability-grid"]');
    expect(grid.exists()).toBe(true);
    expect(grid.text()).toContain("Personal Knowledge RAG");
    expect(grid.text()).toContain("Web Research");
    expect(grid.text()).toContain("planned");
    expect(wrapper.find('[data-testid="landing-login"]').exists()).toBe(true);
  });
});

describe("auth state restore", () => {
  it("restores the user from GET /auth/me", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SESSION_PAYLOAD));
    vi.stubGlobal("fetch", fetchMock);
    await restoreSession();
    expect(authState.user?.username).toBe("alice");
    expect(authState.csrfToken).toBe("csrf-123");
    expect(authState.restoring).toBe(false);
    expect(fetchMock.mock.calls[0][0]).toContain("/api/v2/auth/me");
  });

  it("stays anonymous when /auth/me returns 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r1", error: { code: "AUTHENTICATION_REQUIRED", message: "x" } },
          401,
        ),
      ),
    );
    await restoreSession();
    expect(authState.user).toBeNull();
    expect(authState.restoring).toBe(false);
  });
});

describe("login view", () => {
  it("shows the unified invalid-credentials error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r1", error: { code: "INVALID_CREDENTIALS", message: "x" } },
          401,
        ),
      ),
    );
    const router = await makeRouter();
    const wrapper = mount(LoginView, { global: { plugins: [router] } });
    await wrapper.find('[data-testid="login-username"]').setValue("alice");
    await wrapper.find('[data-testid="login-password"]').setValue("wrong-password-long");
    await wrapper.find('[data-testid="login-form"]').trigger("submit.prevent");
    await flushPromises();
    const error = wrapper.find('[data-testid="login-error"]');
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain("用户名或密码错误");
    expect(authState.user).toBeNull();
  });

  it("navigates to the assistant on success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(SESSION_PAYLOAD)));
    const router = await makeRouter();
    await router.push("/login");
    const wrapper = mount(LoginView, { global: { plugins: [router] } });
    await wrapper.find('[data-testid="login-username"]').setValue("alice");
    await wrapper.find('[data-testid="login-password"]').setValue("correct-password-1");
    await wrapper.find('[data-testid="login-form"]').trigger("submit.prevent");
    await flushPromises();
    expect(authState.user?.username).toBe("alice");
    expect(router.currentRoute.value.name).toBe("assistant");
  });

  it("never stores the session token client-side (login store API)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(SESSION_PAYLOAD)));
    const outcome = await login("alice", "correct-password-1");
    expect(outcome.ok).toBe(true);
    expect(authState.csrfToken).toBe("csrf-123");
    // The only auth artefacts in memory are username/role/csrf token.
    expect(JSON.stringify(authState)).not.toContain("session_token");
  });
});

describe("route guard (UX only)", () => {
  it("redirects anonymous visitors from /assistant to /login", async () => {
    clearAuth();
    const router = await makeRouter();
    await router.push("/assistant");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("login");
  });

  it("lets authenticated visitors reach /assistant", async () => {
    authState.user = { username: "alice", role: "USER" };
    authState.csrfToken = "csrf-123";
    authState.restoring = false;
    const router = await makeRouter();
    await router.push("/assistant");
    await router.isReady();
    expect(router.currentRoute.value.name).toBe("assistant");
  });
});

describe("activation view (fragment transport)", () => {
  it("reads the token from location.hash and wipes it immediately", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ request_id: "r1", result: "account_activated" }));
    vi.stubGlobal("fetch", fetchMock);
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "#token=some-token";
    mount(ActivateView, { global: { plugins: [router] } });
    // The credential must be stripped from the address bar / history.
    expect(window.location.hash).toBe("");
  });

  it("rejects mismatched confirmation before any request", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "#token=some-token";
    const wrapper = mount(ActivateView, {
      global: { plugins: [router] },
    });
    await wrapper.find('[data-testid="activate-password"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-confirmation"]').setValue("different-password");
    await wrapper.find('[data-testid="activate-form"]').trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[data-testid="activate-error"]').text()).toContain("不一致");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits the token once via POST body and shows success", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ request_id: "r1", result: "account_activated" }));
    vi.stubGlobal("fetch", fetchMock);
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "#token=some-token";
    const wrapper = mount(ActivateView, { global: { plugins: [router] } });
    await wrapper.find('[data-testid="activate-password"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-confirmation"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-form"]').trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[data-testid="activate-done"]').exists()).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v2/auth/activate");
    // Token travels only in the POST body, never in the request URL.
    expect(url).not.toContain("some-token");
    expect(JSON.parse(init.body)).toEqual({ token: "some-token", password: "long-enough-password" });
  });

  it("routes purpose=reset fragments to the reset-password endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ request_id: "r1", result: "password_updated" }));
    vi.stubGlobal("fetch", fetchMock);
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "#token=reset-token&purpose=reset";
    const wrapper = mount(ActivateView, { global: { plugins: [router] } });
    await wrapper.find('[data-testid="activate-password"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-confirmation"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-form"]').trigger("submit.prevent");
    await flushPromises();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0];
    expect(url).toContain("/api/v2/auth/reset-password");
    expect(wrapper.find('[data-testid="activate-done"]').exists()).toBe(true);
  });

  it("shows the missing-credential state without a fragment", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "";
    const wrapper = mount(ActivateView, { global: { plugins: [router] } });
    expect(wrapper.find('[data-testid="activate-missing"]').exists()).toBe(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces invalid/expired link failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ request_id: "r1", error: { code: "INVALID_REQUEST", message: "x" } }, 400),
      ),
    );
    const router = await makeRouter();
    await router.push("/activate");
    window.location.hash = "#token=bad-token";
    const wrapper = mount(ActivateView, { global: { plugins: [router] } });
    await wrapper.find('[data-testid="activate-password"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-confirmation"]').setValue("long-enough-password");
    await wrapper.find('[data-testid="activate-form"]').trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.find('[data-testid="activate-error"]').text()).toContain("无效或已过期");
  });
});
