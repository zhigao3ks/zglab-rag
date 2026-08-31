import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createConversation,
  deleteConversation,
  listConversationMessages,
  listConversations,
} from "../src/conversation/api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const conversation = {
  id: 7,
  title: "项目",
  created_at: "2026-08-31T09:00:00Z",
  updated_at: "2026-08-31T10:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("conversation API client", () => {
  it("parses a conversation list", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([conversation]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listConversations()).resolves.toEqual({ ok: true, data: [conversation] });
    expect(fetchMock).toHaveBeenCalledWith("/api/v2/conversations", {
      credentials: "same-origin",
      method: "GET",
    });
  });

  it("sends create CSRF header and JSON body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(conversation));
    vi.stubGlobal("fetch", fetchMock);

    await expect(createConversation("csrf-token", "项目")).resolves.toEqual({
      ok: true,
      data: conversation,
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/v2/conversations", {
      credentials: "same-origin",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf-token",
      },
      body: JSON.stringify({ title: "项目" }),
    });
  });

  it("accepts a delete 204 response", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteConversation("csrf-token", 7)).resolves.toEqual({ ok: true, data: null });
    expect(fetchMock).toHaveBeenCalledWith("/api/v2/conversations/7", {
      credentials: "same-origin",
      method: "DELETE",
      headers: { "X-CSRF-Token": "csrf-token" },
    });
  });

  it("parses owner-scoped message history", async () => {
    const messages = [
      {
        id: 3,
        conversation_id: 7,
        role: "USER",
        content: "问题",
        created_at: "2026-08-31T10:00:00Z",
      },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(messages)));

    await expect(listConversationMessages(7)).resolves.toEqual({ ok: true, data: messages });
  });

  it("maps a NOT_FOUND envelope without exposing a malformed payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { request_id: "r7", error: { code: "NOT_FOUND", message: "ignored" } },
          404,
        ),
      ),
    );

    await expect(listConversationMessages(7)).resolves.toEqual({
      ok: false,
      code: "NOT_FOUND",
      requestId: "r7",
    });
  });

  it("treats malformed success payloads as NETWORK", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ unexpected: true })));

    await expect(listConversations()).resolves.toEqual({
      ok: false,
      code: "NETWORK",
      requestId: null,
    });
  });
});
