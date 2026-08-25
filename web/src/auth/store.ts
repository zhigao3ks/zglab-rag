/**
 * In-memory authentication state (Phase 11).
 *
 * Nothing sensitive is persisted: the session token lives in an HttpOnly
 * cookie, and the CSRF token plus user info stay in memory only. A page
 * refresh restores the state via GET /api/v2/auth/me.
 */

import { reactive } from "vue";
import {
  authLogin,
  authLogout,
  authMe,
  type AuthUser,
} from "./api";
import type { PublicErrorCode } from "../api/contracts";

interface AuthState {
  user: AuthUser | null;
  csrfToken: string | null;
  /** True while the initial /auth/me restore is in flight. */
  restoring: boolean;
}

export const authState = reactive<AuthState>({
  user: null,
  csrfToken: null,
  restoring: true,
});

export function isAuthenticated(): boolean {
  return authState.user !== null;
}

export function getCsrfToken(): string | null {
  return authState.csrfToken;
}

function applySession(user: AuthUser, csrfToken: string): void {
  authState.user = user;
  authState.csrfToken = csrfToken;
}

export function clearAuth(): void {
  authState.user = null;
  authState.csrfToken = null;
}

/** Restore the session after a page refresh; anonymous visitors stay null. */
export async function restoreSession(): Promise<void> {
  authState.restoring = true;
  try {
    const result = await authMe();
    if (result.ok) {
      applySession(result.data.user, result.data.csrf_token);
    } else {
      clearAuth();
    }
  } finally {
    authState.restoring = false;
  }
}

export type LoginOutcome =
  | { ok: true }
  | { ok: false; code: PublicErrorCode | "NETWORK" };

export async function login(username: string, password: string): Promise<LoginOutcome> {
  const result = await authLogin(username, password);
  if (result.ok) {
    applySession(result.data.user, result.data.csrf_token);
    return { ok: true };
  }
  return { ok: false, code: result.code };
}

/** Logout revokes the server session; local state is cleared regardless. */
export async function logout(): Promise<void> {
  await authLogout(authState.csrfToken);
  clearAuth();
}
