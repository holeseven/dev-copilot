// @author lxy
import { type AgentEvent, parseSseStream } from "./sse";

export interface ChatRequest {
  message: string;
  sessionId: string;
  workspaceId: string;
}

export interface StreamChatOptions {
  signal?: AbortSignal;
}

export type ChatEventHandler = (
  event: AgentEvent,
) => void | Promise<void>;

export async function streamChat(
  request: ChatRequest,
  onEvent: ChatEventHandler,
  options: StreamChatOptions = {},
): Promise<void> {
  const response = await fetch("/api/v1/agent/chat", {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message: request.message,
      session_id: request.sessionId,
      workspace_id: request.workspaceId,
    }),
    signal: options.signal,
  });

  if (!response.ok) {
    throw new Error(`Agent request failed with status ${response.status}`);
  }
  if (!response.body) {
    throw new Error("Agent response did not include an event stream");
  }

  for await (const event of parseSseStream(response.body)) {
    await onEvent(event);
  }
}
