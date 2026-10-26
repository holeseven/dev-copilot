// @author lxy
import { describe, expect, it } from "vitest";

import { parseSseStream } from "./sse";

const encoder = new TextEncoder();

function stream(...chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
}

describe("parseSseStream", () => {
  it("parses known and unknown events split across chunks with LF and CRLF", async () => {
    const source = stream(
      "event: mes",
      "sage\r\ndata: {\"token\":\"你",
      "好\"}\r\n\r\nevent: tool\ndata: {\"status\":\"start\"}\n\n",
      "event: approval_required\r\ndata: {\"action\":\"write_file\"}\r\n\r\n",
      "event: error\ndata: {\"message\":\"failed\"}\n\n",
      "event: done\r\ndata: {\"status\":\"completed\"}\r\n\r\n",
      "event: future_event\ndata: {\"value\":7}\n\n",
    );

    const events = [];
    for await (const event of parseSseStream(source)) {
      events.push(event);
    }

    expect(events).toEqual([
      { event: "message", data: { token: "你好" } },
      { event: "tool", data: { status: "start" } },
      { event: "approval_required", data: { action: "write_file" } },
      { event: "error", data: { message: "failed" } },
      { event: "done", data: { status: "completed" } },
      { event: "future_event", data: { value: 7 } },
    ]);
  });

  it("emits the final event when the stream closes without a blank line", async () => {
    const events = [];
    for await (const event of parseSseStream(
      stream("event: done\ndata: {\"status\":\"completed\"}"),
    )) {
      events.push(event);
    }

    expect(events).toEqual([
      { event: "done", data: { status: "completed" } },
    ]);
  });
});
