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

export interface PendingApprovalsRequest {
  sessionId: string;
  workspaceId: string;
}

export interface PendingApprovalData {
  approval_id: string;
  action: string;
  message: string;
  payload?: Record<string, unknown>;
}

export interface PendingApprovalsResponse {
  workspace_id: string;
  session_id: string;
  status: "idle" | "awaiting_approval";
  approvals: PendingApprovalData[];
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

export async function getPendingApprovals(
  request: PendingApprovalsRequest,
  options: StreamChatOptions = {},
): Promise<PendingApprovalsResponse> {
  const response = await fetch("/api/v1/agent/chat/pending", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      workspace_id: request.workspaceId,
      session_id: request.sessionId,
    }),
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`Pending approval request failed with status ${response.status}`);
  return await response.json() as PendingApprovalsResponse;
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
