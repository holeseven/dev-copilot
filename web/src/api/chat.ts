// @author lxy
import { type AgentEvent, parseSseStream } from "./sse";

export interface ChatRequest {
  message: string;
  sessionId: string;
  workspaceId: string;
}

export interface ResumeRequest {
  sessionId: string;
  workspaceId: string;
  approvalId: string;
  approved: boolean;
  reason?: string;
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
  return streamAgentRequest("/api/v1/agent/chat", {
    message: request.message,
    session_id: request.sessionId,
    workspace_id: request.workspaceId,
  }, onEvent, options);
}

export async function streamResume(
  request: ResumeRequest,
  onEvent: ChatEventHandler,
  options: StreamChatOptions = {},
): Promise<void> {
  return streamAgentRequest("/api/v1/agent/chat/resume", {
    session_id: request.sessionId,
    workspace_id: request.workspaceId,
    approval_id: request.approvalId,
    approved: request.approved,
    reason: request.reason,
  }, onEvent, options);
}

async function streamAgentRequest(
  url: string,
  body: Record<string, unknown>,
  onEvent: ChatEventHandler,
  options: StreamChatOptions,
): Promise<void> {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
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
