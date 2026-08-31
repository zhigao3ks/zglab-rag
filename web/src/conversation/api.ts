/**
 * Conversation REST client for /api/v2/conversations (Phase 15A3).
 *
 * Same-origin only, mirroring auth/api.ts: the session travels in the
 * HttpOnly cookie, and state-changing calls attach the in-memory CSRF
 * token. Payload types mirror the Phase 15A2 backend schemas exactly;
 * nothing beyond conversation metadata and message content exists here.
 *
 * Deleted or foreign conversations return the shared NOT_FOUND envelope,
 * so a 404 never discloses whether a conversation id exists.
 */

import type { PublicErrorCode } from "../api/contracts";
import { isPublicErrorCode } from "../api/contracts";

const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ConversationPayload {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessagePayload {
  id: number;
  conversation_id: number;
  role: "USER" | "ASSISTANT";
  content: string;
  created_at: string;
}

export type ConversationApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; code: PublicErrorCode | "NETWORK"; requestId: string | null };

function isConversationPayload(value: unknown): value is ConversationPayload {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as {
    id?: unknown;
    title?: unknown;
    created_at?: unknown;
    updated_at?: unknown;
  };
  return (
    typeof candidate.id === "number" &&
    typeof candidate.title === "string" &&
    typeof candidate.created_at === "string" &&
    typeof candidate.updated_at === "string"
  );
}

function parseConversationList(body: unknown): ConversationPayload[] | null {
  if (!Array.isArray(body)) {
    return null;
  }
  const list: ConversationPayload[] = [];
  for (const item of body) {
    if (!isConversationPayload(item)) {
      return null;
    }
    list.push(item);
  }
  return list;
}

function isConversationMessagePayload(value: unknown): value is ConversationMessagePayload {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as {
    id?: unknown;
    conversation_id?: unknown;
    role?: unknown;
    content?: unknown;
    created_at?: unknown;
  };
  return (
    typeof candidate.id === "number" &&
    typeof candidate.conversation_id === "number" &&
    (candidate.role === "USER" || candidate.role === "ASSISTANT") &&
    typeof candidate.content === "string" &&
    typeof candidate.created_at === "string"
  );
}

function parseMessageList(body: unknown): ConversationMessagePayload[] | null {
  if (!Array.isArray(body)) {
    return null;
  }
  const list: ConversationMessagePayload[] = [];
  for (const item of body) {
    if (!isConversationMessagePayload(item)) {
      return null;
    }
    list.push(item);
  }
  return list;
}

async function request<T>(
  path: string,
  init: RequestInit,
  parse: (body: unknown) => T | null,
  options: { emptyBody?: boolean } = {},
): Promise<ConversationApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: "same-origin",
      ...init,
    });
  } catch {
    return { ok: false, code: "NETWORK", requestId: null };
  }

  if (response.status === 204) {
    return options.emptyBody
      ? { ok: true, data: null as unknown as T }
      : { ok: false, code: "NETWORK", requestId: null };
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

function jsonInit(method: string, body: unknown, csrfToken: string | null): RequestInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (csrfToken !== null) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  return { method, headers, body: JSON.stringify(body) };
}

/** Conversations arrive in backend order (updated_at DESC, id DESC). */
export function listConversations(): Promise<ConversationApiResult<ConversationPayload[]>> {
  return request("/api/v2/conversations", { method: "GET" }, parseConversationList);
}

/** Creates a conversation with a client-chosen static title; no LLM. */
export function createConversation(
  csrfToken: string | null,
  title: string,
): Promise<ConversationApiResult<ConversationPayload>> {
  return request(
    "/api/v2/conversations",
    jsonInit("POST", { title }, csrfToken),
    (body) => (isConversationPayload(body) ? body : null),
  );
}

/** 204 No Content on success; NOT_FOUND when already deleted or foreign. */
export function deleteConversation(
  csrfToken: string | null,
  conversationId: number,
): Promise<ConversationApiResult<null>> {
  const headers: Record<string, string> = {};
  if (csrfToken !== null) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  return request(
    `/api/v2/conversations/${conversationId}`,
    { method: "DELETE", headers },
    () => null,
    { emptyBody: true },
  );
}

/** Owner-scoped message history in stable order (created_at ASC, id ASC). */
export function listConversationMessages(
  conversationId: number,
): Promise<ConversationApiResult<ConversationMessagePayload[]>> {
  return request(
    `/api/v2/conversations/${conversationId}/messages`,
    { method: "GET" },
    parseMessageList,
  );
}
