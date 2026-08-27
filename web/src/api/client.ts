/**
 * Fetch-based SSE client for POST /api/v2/ask/stream (Phase 11).
 *
 * EventSource is not usable for POST endpoints, so this uses fetch +
 * ReadableStream + TextDecoder with the incremental SSE parser.
 *
 * Authentication travels in the HttpOnly session cookie
 * (credentials: "same-origin"); the session-bound CSRF token is attached
 * as X-CSRF-Token and comes from the in-memory auth store only.
 *
 * Error boundary:
 * - Pre-stream failures arrive as non-2xx `application/json` responses
 *   (the public error envelope) and are surfaced as onError.
 * - Post-stream failures arrive as SSE `error` events and are surfaced
 *   as onError as well; the stream stops, no completed is awaited.
 * - fetch rejections, unexpected closes and malformed payloads become
 *   onNetworkFailure; raw payloads are never exposed to the UI.
 */

import type {
  AskMode,
  PublicErrorCode,
  PublicAskResponse,
  StreamStage,
} from "./contracts";
import { isPublicErrorCode } from "./contracts";
import { SseIncrementalParser, StreamPayloadError, mapRawEvent } from "./sse";
import { getCsrfToken } from "../auth/store";

export interface AskStreamCallbacks {
  onStage(stage: StreamStage, requestId: string): void;
  onCompleted(completed: PublicAskResponse): void;
  onError(code: PublicErrorCode, requestId: string): void;
  onNetworkFailure(): void;
}

const API_BASE: string = import.meta.env.VITE_API_BASE_URL ?? "";

export function askStreamUrl(): string {
  return `${API_BASE}/api/v2/ask/stream`;
}

interface EnvelopeLike {
  request_id?: unknown;
  error?: { code?: unknown; message?: unknown };
}

function extractEnvelope(body: unknown): { code: PublicErrorCode; requestId: string } | null {
  if (typeof body !== "object" || body === null) {
    return null;
  }
  const envelope = body as EnvelopeLike;
  const code = envelope.error?.code;
  if (!isPublicErrorCode(code)) {
    return null;
  }
  return {
    code,
    requestId: typeof envelope.request_id === "string" ? envelope.request_id : "",
  };
}

/**
 * Run one ask/stream request. Resolves when the stream is finished for
 * any reason (completed, error event, pre-stream JSON error, network
 * failure or abort); it never throws for visitor-facing failures.
 */
export async function askStream(
  question: string,
  callbacks: AskStreamCallbacks,
  signal: AbortSignal,
  mode: AskMode = "auto",
): Promise<void> {
  let response: Response;
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const csrfToken = getCsrfToken();
    if (csrfToken !== null) {
      headers["X-CSRF-Token"] = csrfToken;
    }
    response = await fetch(askStreamUrl(), {
      method: "POST",
      credentials: "same-origin",
      headers,
      body: JSON.stringify({ question, mode }),
      signal,
    });
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      return; // Component unmount / navigation; nothing to show.
    }
    callbacks.onNetworkFailure();
    return;
  }

  if (!response.ok) {
    // Pre-stream error: the SSE stream was never opened.
    let parsed: { code: PublicErrorCode; requestId: string } | null = null;
    try {
      parsed = extractEnvelope(await response.json());
    } catch {
      parsed = null;
    }
    if (parsed !== null) {
      callbacks.onError(parsed.code, parsed.requestId);
    } else {
      callbacks.onNetworkFailure();
    }
    return;
  }

  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("text/event-stream")) {
    // Unexpected non-stream success response: treat as a protocol error.
    callbacks.onNetworkFailure();
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onNetworkFailure();
    return;
  }

  const decoder = new TextDecoder("utf-8");
  const parser = new SseIncrementalParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      const chunk = decoder.decode(value, { stream: true });
      let rawEvents;
      try {
        rawEvents = parser.feed(chunk);
      } catch {
        callbacks.onNetworkFailure();
        return;
      }
      for (const raw of rawEvents) {
        try {
          const event = mapRawEvent(raw);
          if (event === null) {
            continue; // Unknown events are ignored.
          }
          if (event.kind === "stage") {
            callbacks.onStage(event.stage, event.requestId);
          } else if (event.kind === "completed") {
            callbacks.onCompleted(event.completed);
            return;
          } else {
            callbacks.onError(event.error.error.code, event.error.request_id);
            return;
          }
        } catch (error) {
          if (error instanceof StreamPayloadError) {
            callbacks.onNetworkFailure();
            return;
          }
          throw error;
        }
      }
    }
    // Stream closed. If it ended before a terminal event, the connection
    // dropped unexpectedly.
    callbacks.onNetworkFailure();
  } catch (error) {
    if ((error as Error).name === "AbortError") {
      return;
    }
    callbacks.onNetworkFailure();
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // Already released; ignore.
    }
  }
}
