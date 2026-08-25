/**
 * Auth REST client for /api/v2/auth (Phase 11).
 *
 * Same-origin only: cookies travel automatically with
 * `credentials: "same-origin"`; the session token itself is HttpOnly and
 * never visible to JavaScript. The CSRF token arrives in response bodies
 * and is kept in memory by the auth store, never in localStorage.
 */

import type { PublicErrorCode } from "../api/contracts";
import { isPublicErrorCode } from "../api/contracts";

const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

export interface AuthUser {
  username: string;
  role: "ADMIN" | "USER";
}

export interface AuthSessionPayload {
  request_id: string;
  user: AuthUser;
  csrf_token: string;
}

export interface AuthResultPayload {
  request_id: string;
  result: "logged_out" | "account_activated" | "password_updated" | "password_changed";
}

export type AuthApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; code: PublicErrorCode | "NETWORK"; requestId: string | null };

function isAuthUser(value: unknown): value is AuthUser {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as { username?: unknown; role?: unknown };
  return (
    typeof candidate.username === "string" &&
    (candidate.role === "ADMIN" || candidate.role === "USER")
  );
}

function parseSessionPayload(body: unknown): AuthSessionPayload | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const candidate = body as {
    request_id?: unknown;
    user?: unknown;
    csrf_token?: unknown;
  };
  if (
    typeof candidate.request_id !== "string" ||
    typeof candidate.csrf_token !== "string" ||
    !isAuthUser(candidate.user)
  ) {
    return null;
  }
  return candidate as AuthSessionPayload;
}

async function request<T>(
  path: string,
  init: RequestInit,
  parse: (body: unknown) => T | null,
): Promise<AuthApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: "same-origin",
      ...init,
    });
  } catch {
    return { ok: false, code: "NETWORK", requestId: null };
  }

  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const envelope = body as {
      request_id?: unknown;
      error?: { code?: unknown };
    } | null;
    const code = envelope?.error?.code;
    return {
      ok: false,
      code: isPublicErrorCode(code) ? code : "NETWORK",
      requestId: typeof envelope?.request_id === "string" ? envelope.request_id : null,
    };
  }

  const parsed = parse(body);
  if (parsed === null) {
    return { ok: false, code: "NETWORK", requestId: null };
  }
  return { ok: true, data: parsed };
}

function jsonInit(body: unknown, csrfToken: string | null): RequestInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrfToken !== null) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  return { method: "POST", headers, body: JSON.stringify(body) };
}

export function authMe(): Promise<AuthApiResult<AuthSessionPayload>> {
  return request("/api/v2/auth/me", { method: "GET" }, parseSessionPayload);
}

export function authLogin(
  username: string,
  password: string,
): Promise<AuthApiResult<AuthSessionPayload>> {
  return request(
    "/api/v2/auth/login",
    jsonInit({ username, password }, null),
    parseSessionPayload,
  );
}

export function authLogout(csrfToken: string | null): Promise<AuthApiResult<AuthResultPayload>> {
  return request("/api/v2/auth/logout", jsonInit({}, csrfToken), (body) =>
    typeof body === "object" && body !== null ? (body as AuthResultPayload) : null,
  );
}

export function authActivate(
  token: string,
  password: string,
): Promise<AuthApiResult<AuthResultPayload>> {
  return request("/api/v2/auth/activate", jsonInit({ token, password }, null), (body) =>
    typeof body === "object" && body !== null ? (body as AuthResultPayload) : null,
  );
}

/** Purpose-pinned endpoint: accepts RESET_PASSWORD tokens only. */
export function authResetPassword(
  token: string,
  password: string,
): Promise<AuthApiResult<AuthResultPayload>> {
  return request("/api/v2/auth/reset-password", jsonInit({ token, password }, null), (body) =>
    typeof body === "object" && body !== null ? (body as AuthResultPayload) : null,
  );
}

export function authChangePassword(
  csrfToken: string | null,
  currentPassword: string,
  newPassword: string,
): Promise<AuthApiResult<AuthResultPayload>> {
  return request(
    "/api/v2/auth/change-password",
    jsonInit({ current_password: currentPassword, new_password: newPassword }, csrfToken),
    (body) => (typeof body === "object" && body !== null ? (body as AuthResultPayload) : null),
  );
}
