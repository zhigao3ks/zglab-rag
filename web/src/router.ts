/**
 * Vue Router setup (Phase 11).
 *
 * Access model: public landing / login / activation are anonymous; the
 * assistant requires authentication. Route guards are UX only — every
 * capability is enforced server-side by /api/v2.
 */

import { createRouter, createWebHistory } from "vue-router";
import { authState } from "./auth/store";
import ActivateView from "./views/ActivateView.vue";
import AssistantView from "./views/AssistantView.vue";
import LandingView from "./views/LandingView.vue";
import LoginView from "./views/LoginView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "landing", component: LandingView },
    { path: "/login", name: "login", component: LoginView },
    // The one-time token travels in the URL FRAGMENT (#token=...), never
    // in the path or query: fragments are not sent to the server, so the
    // credential stays out of Nginx access logs and Referer headers.
    { path: "/activate", name: "activate", component: ActivateView },
    {
      path: "/assistant",
      name: "assistant",
      component: AssistantView,
      meta: { requiresAuth: true },
    },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});

router.beforeEach((to) => {
  if (to.meta.requiresAuth && authState.user === null) {
    return { name: "login" };
  }
  if (to.name === "login" && authState.user !== null) {
    return { name: "assistant" };
  }
  return true;
});
