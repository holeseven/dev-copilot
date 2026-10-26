// @author lxy
import { afterEach, describe, expect, it, vi } from "vitest";

import { streamChat } from "./chat";

const encoder = new TextEncoder();

function sseResponse(body: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamChat", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts the snake_case request to the Java SSE endpoint and forwards the signal", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(
      "event: message\ndata: {\"token\":\"hello\"}\n\n" +
      "event: done\ndata: {\"status\":\"completed\"}\n\n",
    ));
    vi.stubGlobal("fetch", fetchMock);
    const events: unknown[] = [];

    await streamChat(
      {
        message: "inspect",
        sessionId: "session-1",
        workspaceId: "workspace-1",
      },
      (event) => {
        events.push(event);
      },
      { signal },
    );

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/agent/chat");
    expect(init.method).toBe("POST");
    expect(init.signal).toBe(signal);
    expect(init.headers).toEqual({
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    });
    expect(JSON.parse(init.body as string)).toEqual({
      message: "inspect",
      session_id: "session-1",
      workspace_id: "workspace-1",
    });
    expect(events).toEqual([
      { event: "message", data: { token: "hello" } },
      { event: "done", data: { status: "completed" } },
    ]);
  });
});
