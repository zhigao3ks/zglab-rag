/**
 * Fetch SSE client tests. fetch is mocked; no real backend is touched.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { askStream, type AskStreamCallbacks } from "../src/api/client";

function makeCallbacks() {
  return {
    onStage: vi.fn(),
    onCompleted: vi.fn(),
    onError: vi.fn(),
    onNetworkFailure: vi.fn(),
  } satisfies AskStreamCallbacks;
}

function sseBody(text: string): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(text));
      controller.close();
    },
  });
}

function sseResponse(text: string): Response {
  return new Response(sseBody(text), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const STAGES =
  "event: accepted\ndata: {\"request_id\":\"r1\",\"stage\":\"accepted\"}\n\n" +
  ": keep-alive\n\n" +
  "event: retrieving\ndata: {\"request_id\":\"r1\",\"stage\":\"retrieving\"}\n\n" +
  "event: generating\ndata: {\"request_id\":\"r1\",\"stage\":\"generating\"}\n\n" +
  "event: validating\ndata: {\"request_id\":\"r1\",\"stage\":\"validating\"}\n\n";

const COMPLETED =
  "event: completed\ndata: {\"request_id\":\"r1\",\"status\":\"answered\"," +
  "\"answer\":\"这是回答。\",\"sources\":[{\"id\":\"E1\",\"title\":\"标题\"," +
  "\"section\":[\"小节\"],\"source_path\":\"a.md\"}]}\n\n";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("askStream", () => {
  it("delivers stages in order, ignores heartbeats, then completed", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(STAGES + COMPLETED)));
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);

    expect(callbacks.onStage.mock.calls.map((call) => call[0])).toEqual([
      "accepted",
      "retrieving",
      "generating",
      "validating",
    ]);
    expect(callbacks.onCompleted).toHaveBeenCalledTimes(1);
    expect(callbacks.onCompleted.mock.calls[0][0].answer).toEqual("这是回答。");
    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onNetworkFailure).not.toHaveBeenCalled();
  });

  it("sends only the question plus default mode, never conversation history", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(STAGES + COMPLETED));
    vi.stubGlobal("fetch", fetchMock);
    await askStream("第二个问题？", makeCallbacks(), new AbortController().signal);

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ question: "第二个问题？", mode: "auto" });
  });

  it("forwards an explicit capability mode when chosen", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(STAGES + COMPLETED));
    vi.stubGlobal("fetch", fetchMock);
    await askStream("问题？", makeCallbacks(), new AbortController().signal, "web");

    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ question: "问题？", mode: "web" });
  });

  it("maps pre-stream JSON errors (rate limited)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r9", error: { code: "RATE_LIMITED", message: "too many" } },
          429,
        ),
      ),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith("RATE_LIMITED", "r9");
    expect(callbacks.onCompleted).not.toHaveBeenCalled();
    expect(callbacks.onNetworkFailure).not.toHaveBeenCalled();
  });

  it("maps pre-stream JSON errors (service busy)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r8", error: { code: "SERVICE_BUSY", message: "busy" } },
          503,
        ),
      ),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onError).toHaveBeenCalledWith("SERVICE_BUSY", "r8");
  });

  it("maps post-stream SSE error events and stops waiting", async () => {
    const errorEvent =
      "event: error\ndata: {\"request_id\":\"r1\",\"error\":" +
      "{\"code\":\"GENERATION_TIMEOUT\",\"message\":\"timeout\"}}\n\n";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(STAGES + errorEvent)));
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith("GENERATION_TIMEOUT", "r1");
    expect(callbacks.onCompleted).not.toHaveBeenCalled();
  });

  it("reports network failure on fetch rejection without exposing messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onNetworkFailure).toHaveBeenCalledTimes(1);
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("reports network failure on malformed SSE payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(sseResponse("event: completed\ndata: not-json\n\n")),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onNetworkFailure).toHaveBeenCalledTimes(1);
    expect(callbacks.onCompleted).not.toHaveBeenCalled();
  });

  it("reports network failure when the stream closes before a terminal event", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(STAGES)));
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onNetworkFailure).toHaveBeenCalledTimes(1);
  });

  it("stays silent on abort", async () => {
    const abortError = new Error("aborted");
    abortError.name = "AbortError";
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abortError));
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onNetworkFailure).not.toHaveBeenCalled();
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("handles UTF-8 Chinese split across byte chunks", async () => {
    const encoder = new TextEncoder();
    const full =
      "event: completed\ndata: {\"request_id\":\"r1\",\"status\":\"answered\"," +
      "\"answer\":\"中文回答\",\"sources\":[]}\n\n";
    const bytes = encoder.encode(full);
    const half = Math.floor(bytes.length / 2);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, half));
        controller.enqueue(bytes.slice(half));
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onCompleted.mock.calls[0][0].answer).toEqual("中文回答");
  });

  it("handles a multibyte UTF-8 character split at an exact byte boundary", async () => {
    // Deterministically cut inside the three-byte encoding of "文" so the
    // first chunk ends mid-character; TextDecoder(stream: true) must buffer
    // the partial codepoint.
    const encoder = new TextEncoder();
    const full =
      "event: completed\ndata: {\"request_id\":\"r1\",\"status\":\"answered\"," +
      "\"answer\":\"中文\",\"sources\":[]}\n\n";
    const bytes = encoder.encode(full);
    const splitAt = bytes.indexOf(0xE6); // first byte of "文"
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, splitAt + 1));
        controller.enqueue(bytes.slice(splitAt + 1));
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onCompleted.mock.calls[0][0].answer).toEqual("中文");
  });

  it("maps pre-stream INVALID_REQUEST errors without SSE parsing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r7", error: { code: "INVALID_REQUEST", message: "too long" } },
          400,
        ),
      ),
    );
    const callbacks = makeCallbacks();
    await askStream("问题？", callbacks, new AbortController().signal);
    expect(callbacks.onError).toHaveBeenCalledWith("INVALID_REQUEST", "r7");
    expect(callbacks.onNetworkFailure).not.toHaveBeenCalled();
    expect(callbacks.onCompleted).not.toHaveBeenCalled();
  });
});
