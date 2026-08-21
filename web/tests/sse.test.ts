/**
 * SSE incremental parser tests — the Phase 9C testing focus.
 * Network chunks may split events at any boundary; the parser must
 * reassemble them, emit multiple events from one chunk, ignore
 * heartbeats, and never evaluate payloads.
 */

import { describe, expect, it } from "vitest";
import { SseIncrementalParser, StreamPayloadError, mapRawEvent } from "../src/api/sse";

function sseEvent(event: string, data: string): string {
  return `event: ${event}\ndata: ${data}\n\n`;
}

describe("SseIncrementalParser", () => {
  it("reassembles an event split across two network chunks", () => {
    const parser = new SseIncrementalParser();
    const data = JSON.stringify({ request_id: "r1", stage: "retrieving" });

    expect(parser.feed("event: retriev")).toEqual([]);
    const events = parser.feed(`ing\ndata: ${data}\n\n`);

    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ event: "retrieving", data });
  });

  it("emits multiple events contained in one chunk", () => {
    const parser = new SseIncrementalParser();
    const chunk =
      sseEvent("accepted", JSON.stringify({ request_id: "r1", stage: "accepted" })) +
      sseEvent("retrieving", JSON.stringify({ request_id: "r1", stage: "retrieving" }));

    const events = parser.feed(chunk);
    expect(events.map((event) => event.event)).toEqual(["accepted", "retrieving"]);
  });

  it("ignores heartbeat comments", () => {
    const parser = new SseIncrementalParser();
    const events = parser.feed(": keep-alive\n\n");
    expect(events).toEqual([]);
  });

  it("keeps heartbeat from attaching to the following event", () => {
    const parser = new SseIncrementalParser();
    const chunk =
      ": keep-alive\n\n" +
      sseEvent("generating", JSON.stringify({ request_id: "r1", stage: "generating" }));
    const events = parser.feed(chunk);
    expect(events).toHaveLength(1);
    expect(events[0].event).toEqual("generating");
  });

  it("handles data split mid-JSON across many chunks", () => {
    const parser = new SseIncrementalParser();
    const payload = JSON.stringify({ request_id: "r1", stage: "validating" });
    const full = `event: validating\ndata: ${payload}\n\n`;
    const collected = [];
    for (const char of full) {
      collected.push(...parser.feed(char));
    }
    expect(collected).toHaveLength(1);
    expect(collected[0].data).toEqual(payload);
  });

  it("normalizes CRLF line endings", () => {
    const parser = new SseIncrementalParser();
    const events = parser.feed("event: accepted\r\ndata: {}\r\n\r\n");
    expect(events).toEqual([{ event: "accepted", data: "{}" }]);
  });

  it("drops an incomplete trailing event on flush", () => {
    const parser = new SseIncrementalParser();
    parser.feed("event: completed\ndata: {\"partial");
    expect(parser.flush()).toEqual([]);
  });

  it("decodes UTF-8 Chinese payloads round-trip", () => {
    const parser = new SseIncrementalParser();
    const payload = JSON.stringify({ answer: "这是中文回答。" });
    const events = parser.feed(sseEvent("completed", payload));
    expect(JSON.parse(events[0].data).answer).toEqual("这是中文回答。");
  });
});

describe("mapRawEvent", () => {
  it("maps stage events with typed requestId", () => {
    const event = mapRawEvent({
      event: "retrieving",
      data: JSON.stringify({ request_id: "r1", stage: "retrieving" }),
    });
    expect(event).toEqual({ kind: "stage", stage: "retrieving", requestId: "r1" });
  });

  it("maps completed events with sources", () => {
    const event = mapRawEvent({
      event: "completed",
      data: JSON.stringify({
        request_id: "r1",
        status: "answered",
        answer: "回答",
        sources: [
          { id: "E1", title: "标题", section: ["A", "B"], source_path: "a.md" },
        ],
      }),
    });
    expect(event?.kind).toEqual("completed");
    if (event?.kind === "completed") {
      expect(event.completed.sources[0].title).toEqual("标题");
    }
  });

  it("maps error events", () => {
    const event = mapRawEvent({
      event: "error",
      data: JSON.stringify({
        request_id: "r1",
        error: { code: "GENERATION_TIMEOUT", message: "timeout" },
      }),
    });
    expect(event).toEqual({
      kind: "error",
      error: {
        request_id: "r1",
        error: { code: "GENERATION_TIMEOUT", message: "timeout" },
      },
    });
  });

  it("ignores unknown event names", () => {
    expect(mapRawEvent({ event: "mystery", data: "{}" })).toBeNull();
  });

  it("raises a controlled error on invalid JSON instead of eval", () => {
    expect(() => mapRawEvent({ event: "completed", data: "not-json" })).toThrow(
      StreamPayloadError,
    );
  });

  it("raises a controlled error on wrong stage values", () => {
    expect(() =>
      mapRawEvent({
        event: "generating",
        data: JSON.stringify({ request_id: "r1", stage: "hacking" }),
      }),
    ).toThrow(StreamPayloadError);
  });

  it("keeps malicious SSE-looking user text as inert data", () => {
    // Even if a validated answer contained SSE-like text, it arrives as a
    // JSON string inside one data line and never becomes a new event.
    const malicious = '回答\nevent: fake\ndata: {"evil":true}';
    const events = new SseIncrementalParser().feed(
      sseEvent("completed", JSON.stringify({
        request_id: "r1",
        status: "answered",
        answer: malicious,
        sources: [],
      })),
    );
    expect(events).toHaveLength(1);
    expect(events[0].event).toEqual("completed");
    const mapped = mapRawEvent(events[0]);
    if (mapped?.kind === "completed") {
      expect(mapped.completed.answer).toEqual(malicious);
    }
  });
});
