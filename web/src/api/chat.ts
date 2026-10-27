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

export interface ApprovalOutcomeRequest {
  sessionId: string;
  workspaceId: string;
  approvalId: string;
}

export const APPROVAL_OUTCOME_STATUSES = [
  "applying",
  "rejected",
  "failed",
  "recovery_required",
  "applied_recovered",
  "applied_and_tests_passed",
  "applied_tests_failed",
  "applied_tests_timed_out",
  "applied_test_termination_failed",
  "applied_test_error",
] as const;

export type ApprovalOutcomeStatus = typeof APPROVAL_OUTCOME_STATUSES[number];

export interface ApprovalOutcomeMutation {
  proposal_id: string;
  workspace_id: string;
  virtual_path: string;
  operation: "create" | "replace";
  before_sha256: string | null;
  after_sha256: string;
}

export interface ApprovalOutcomeValidation {
  profile: string;
  status: "passed" | "failed" | "timed_out" | "termination_failed" | "error";
  exit_code: number | null;
  truncated: boolean;
}

export interface ApprovalOutcomeResponse {
  workspace_id: string;
  session_id: string;
  approval_id: string;
  status: ApprovalOutcomeStatus;
  terminal: boolean;
  mutation?: ApprovalOutcomeMutation;
  validation?: ApprovalOutcomeValidation;
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
  outcomes?: ApprovalOutcomeResponse[];
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

export async function getApprovalOutcome(
  request: ApprovalOutcomeRequest,
  options: StreamChatOptions = {},
): Promise<ApprovalOutcomeResponse> {
  const response = await fetch("/api/v1/agent/chat/outcome", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      workspace_id: request.workspaceId,
      session_id: request.sessionId,
      approval_id: request.approvalId,
    }),
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`Approval outcome request failed with status ${response.status}`);
  const value: unknown = await response.json();
  return parseApprovalOutcomeResponse(value, request);
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

export function parseApprovalOutcomeResponse(
  value: unknown,
  request: ApprovalOutcomeRequest,
): ApprovalOutcomeResponse {
  const data = strictRecord(value, [
    "workspace_id", "session_id", "approval_id", "status", "terminal",
  ], ["mutation", "validation"]);
  const status = data.status;
  if (data.workspace_id !== request.workspaceId
    || data.session_id !== request.sessionId
    || data.approval_id !== request.approvalId
    || typeof status !== "string"
    || !APPROVAL_OUTCOME_STATUSES.includes(status as ApprovalOutcomeStatus)
    || typeof data.terminal !== "boolean") invalidOutcome();

  const typedStatus = status as ApprovalOutcomeStatus;
  const expectedTerminal = typedStatus !== "applying";
  if (data.terminal !== expectedTerminal) invalidOutcome();

  const mutation = data.mutation === undefined
    ? undefined : parseMutation(data.mutation, request);
  const validation = data.validation === undefined
    ? undefined : parseValidation(data.validation);
  const applied = typedStatus.startsWith("applied_");
  const tested = typedStatus !== "applied_recovered" && applied;
  if ((applied && mutation === undefined)
    || (!applied && mutation !== undefined)
    || (tested && validation === undefined)
    || (!tested && validation !== undefined)) invalidOutcome();
  const expectedValidation = ({
    applied_and_tests_passed: "passed",
    applied_tests_failed: "failed",
    applied_tests_timed_out: "timed_out",
    applied_test_termination_failed: "termination_failed",
    applied_test_error: "error",
  } as Partial<Record<ApprovalOutcomeStatus, ApprovalOutcomeValidation["status"]>>)[typedStatus];
  if (expectedValidation && validation?.status !== expectedValidation) invalidOutcome();

  return {
    workspace_id: request.workspaceId,
    session_id: request.sessionId,
    approval_id: request.approvalId,
    status: typedStatus,
    terminal: expectedTerminal,
    ...(mutation ? { mutation } : {}),
    ...(validation ? { validation } : {}),
  };
}

function parseMutation(value: unknown, request: ApprovalOutcomeRequest): ApprovalOutcomeMutation {
  const data = strictRecord(value, [
    "proposal_id", "workspace_id", "virtual_path", "operation",
    "before_sha256", "after_sha256",
  ]);
  if (data.proposal_id !== request.approvalId
    || data.workspace_id !== request.workspaceId
    || typeof data.virtual_path !== "string"
    || !safeRelativePath(data.virtual_path)
    || (data.operation !== "create" && data.operation !== "replace")
    || !nullableSha256(data.before_sha256)
    || !sha256(data.after_sha256)) invalidOutcome();
  return data as unknown as ApprovalOutcomeMutation;
}

function parseValidation(value: unknown): ApprovalOutcomeValidation {
  const data = strictRecord(value, ["profile", "status", "exit_code", "truncated"]);
  const statuses = ["passed", "failed", "timed_out", "termination_failed", "error"];
  if (typeof data.profile !== "string" || !data.profile || data.profile.length > 128
    || typeof data.status !== "string" || !statuses.includes(data.status)
    || (data.exit_code !== null
      && (typeof data.exit_code !== "number" || !Number.isInteger(data.exit_code)))
    || typeof data.truncated !== "boolean") invalidOutcome();
  return data as unknown as ApprovalOutcomeValidation;
}

function strictRecord(
  value: unknown,
  required: string[],
  optional: string[] = [],
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) invalidOutcome();
  const data = value as Record<string, unknown>;
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !Object.prototype.hasOwnProperty.call(data, key))
    || Object.keys(data).some((key) => !allowed.has(key))) invalidOutcome();
  return data;
}

function safeRelativePath(value: string) {
  if (!value || value.length > 1024 || value.includes("\\") || value.charCodeAt(0) === 47
    || /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)
    || /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/.test(value)) return false;
  return value.split(String.fromCharCode(47)).every((part) => part !== "" && part !== "." && part !== "..");
}

function sha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/i.test(value);
}

function nullableSha256(value: unknown): value is string | null {
  return value === null || sha256(value);
}

function invalidOutcome(): never {
  throw new Error("Invalid approval outcome response");
}
