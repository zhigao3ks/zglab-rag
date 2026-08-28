/**
 * Incremental SSE parser for POST-based status streaming.
 *
 * EventSource cannot be used because the backend endpoint is POST
 * /api/v1/ask/stream; the client uses fetch + ReadableStream and this
 * parser. Network chunks may split an SSE event at any byte boundary,
 * so the parser maintains an internal line buffer across feed() calls.
 *
 * Supported lines: `event:`, `data:` and SSE comments (`: keep-alive`
 * heartbeats, ignored). An event is dispatched on a blank line. JSON
 * parsing is done by the caller via mapRawEvent, never eval().
 */

import type {
  PublicAskResponse,
  PublicErrorResponse,
  PublicStreamStatus,
  StreamEvent,
} from "./contracts";
import { isPublicErrorCode, isStreamStage } from "./contracts";

export interface RawSseEvent {
  event: string;
  data: string;
}

export class SseIncrementalParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];
  private hasFields = false;

  /** Feed one network chunk; returns every event completed inside it. */
  feed(chunk: string): RawSseEvent[] {
    this.buffer += chunk;
    const events: RawSseEvent[] = [];
    // Normalize CRLF; keep the trailing partial line in the buffer. A
    // trailing \r is ambiguous (standalone CR line ending vs the first half
    // of a CRLF split across chunks), so it is kept verbatim in the buffer
    // and only resolved once the next chunk arrives.
    const pendingCr = this.buffer.endsWith("\r");
    const scannable = pendingCr ? this.buffer.slice(0, -1) : this.buffer;
    const normalized = scannable.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const lines = normalized.split("\n");
    this.buffer = (lines.pop() ?? "") + (pendingCr ? "\r" : "");
    for (const line of lines) {
      const finished = this.consumeLine(line);
      if (finished !== null) {
        events.push(finished);
      }
    }
    return events;
  }

  /**
   * Flush at stream end. Per the SSE spec, an event without a terminating
   * blank line is never dispatched, so anything left in the buffer is
   * incomplete and deliberately dropped.
   */
  flush(): RawSseEvent[] {
    // Resolve a pending trailing CR as a standalone line break before
    // discarding the incomplete remainder.
    if (this.buffer.endsWith("\r")) {
      this.consumeLine(this.buffer.slice(0, -1));
    }
    this.buffer = "";
    this.resetEvent();
    return [];
  }

  private consumeLine(line: string): RawSseEvent | null {
    if (line === "") {
      // Blank line: dispatch if any field was seen, then reset.
      if (!this.hasFields) {
        return null;
      }
      const finished: RawSseEvent = {
        event: this.eventName || "message",
        data: this.dataLines.join("\n"),
      };
      this.resetEvent();
      return finished;
    }
    if (line.startsWith(":")) {
      // SSE comment (e.g. ": keep-alive" heartbeat): ignore completely.
      return null;
    }
    const colonIndex = line.indexOf(":");
    let field: string;
    let value: string;
    if (colonIndex === -1) {
      field = line;
      value = "";
    } else {
      field = line.slice(0, colonIndex);
      value = line.slice(colonIndex + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }
    }
    this.hasFields = true;
    if (field === "event") {
      this.eventName = value;
    } else if (field === "data") {
      this.dataLines.push(value);
    }
    // Unknown fields (id:, retry:) are ignored.
    return null;
  }

  private resetEvent(): void {
    this.eventName = "";
    this.dataLines = [];
    this.hasFields = false;
  }
}

/** Thrown when an SSE payload cannot be turned into a typed event. */
export class StreamPayloadError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StreamPayloadError";
  }
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    throw new StreamPayloadError("invalid JSON payload");
  }
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new StreamPayloadError("payload is not an object");
  }
  return value as Record<string, unknown>;
}

/**
 * Map a raw SSE event to the typed public contract. Unknown events are
 * ignored (null). Malformed payloads raise StreamPayloadError so the
 * client can show a controlled error instead of rendering raw data.
 */
export function mapRawEvent(raw: RawSseEvent): StreamEvent | null {
  switch (raw.event) {
    case "accepted":
    case "retrieving":
    case "researching":
    case "planning":
    case "executing":
    case "synthesizing":
    case "generating":
    case "validating": {
      const payload = asRecord(parseJson(raw.data)) as Partial<PublicStreamStatus>;
      if (!isStreamStage(payload.stage) || typeof payload.request_id !== "string") {
        throw new StreamPayloadError("invalid stage payload");
      }
      return { kind: "stage", stage: payload.stage, requestId: payload.request_id };
    }
    case "completed": {
      const payload = asRecord(parseJson(raw.data)) as Partial<PublicAskResponse>;
      if (
        typeof payload.request_id !== "string" ||
        (payload.status !== "answered" && payload.status !== "insufficient_evidence") ||
        typeof payload.answer !== "string" ||
        !Array.isArray(payload.sources)
      ) {
        throw new StreamPayloadError("invalid completed payload");
      }
      const sources = payload.sources.map((source) => {
        const record = asRecord(source);
        if (
          typeof record.id !== "string" ||
          typeof record.title !== "string" ||
          typeof record.source_path !== "string" ||
          !Array.isArray(record.section)
        ) {
          throw new StreamPayloadError("invalid source payload");
        }
        // Phase 12D additive fields stay optional and strictly typed:
        // origin is either personal or web; url/domain are strings or null.
        const origin: "personal" | "web" | undefined =
          record.origin === "web"
            ? "web"
            : record.origin === "personal"
              ? "personal"
              : undefined;
        const url =
          typeof record.url === "string" ? record.url : record.url === null ? null : undefined;
        const domain =
          typeof record.domain === "string"
            ? record.domain
            : record.domain === null
              ? null
              : undefined;
        return {
          id: record.id,
          title: record.title,
          section: record.section.map((part) => String(part)),
          source_path: record.source_path,
          origin,
          url,
          domain,
        };
      });
      return {
        kind: "completed",
        completed: {
          request_id: payload.request_id,
          status: payload.status,
          answer: payload.answer,
          sources,
        },
      };
    }
    case "error": {
      const payload = asRecord(parseJson(raw.data)) as Partial<PublicErrorResponse>;
      const detail = asRecord(payload.error);
      if (!isPublicErrorCode(detail.code) || typeof payload.request_id !== "string") {
        throw new StreamPayloadError("invalid error payload");
      }
      return {
        kind: "error",
        error: {
          request_id: payload.request_id,
          error: { code: detail.code, message: typeof detail.message === "string" ? detail.message : "" },
        },
      };
    }
    default:
      // Unknown event names are ignored; heartbeats never reach here
      // because comments are dropped by the parser.
      return null;
  }
}
