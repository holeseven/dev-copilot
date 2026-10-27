// @author lxy

const WORKSPACES_URL = "/api/v1/workspaces";

export type KnowledgeIndexState = "not_indexed" | "queued" | "building" | "ready" | "stale" | "failed";

export interface KnowledgeIndexStatus {
  workspaceId: string;
  state: KnowledgeIndexState;
  indexGeneration: string | null;
  jobId: string | null;
  repositoryCount: number;
  fileCount: number;
  chunkCount: number;
  updatedAt: string | null;
  errorCode: string | null;
}

export interface RagRequestOptions {
  signal?: AbortSignal;
}

export async function getKnowledgeIndex(
  workspaceId: string,
  options: RagRequestOptions = {},
): Promise<KnowledgeIndexStatus> {
  const response = await fetch(indexUrl(workspaceId), {
    method: "GET",
    headers: { Accept: "application/json" },
    signal: options.signal,
  });
  if (!response.ok) throw new Error(`Knowledge index request failed with status ${response.status}`);
  return parseStatus(await response.json(), workspaceId);
}

export async function rebuildKnowledgeIndex(
  workspaceId: string,
  options: RagRequestOptions = {},
): Promise<KnowledgeIndexStatus> {
  const rebuildUrl = `${indexUrl(workspaceId)}/rebuild`;
  const response = await fetch(rebuildUrl, {
    method: "POST",
    headers: { Accept: "application/json" },
    signal: options.signal,
  });
  if (response.status !== 202) {
    throw new Error(`Knowledge index rebuild must return 202, received ${response.status}`);
  }
  const status = parseStatus(await response.json(), workspaceId);
  if (status.state !== "queued" || !status.jobId) {
    throw new Error("Knowledge index rebuild response is invalid");
  }
  return status;
}

function indexUrl(workspaceId: string) {
  return `${WORKSPACES_URL}/${encodeURIComponent(workspaceId)}/rag/index`;
}

function parseStatus(value: unknown, workspaceId: string): KnowledgeIndexStatus {
  const data = record(value);
  if (data.workspace_id !== workspaceId) throw new Error("Knowledge index workspace mismatch");
  if (!isState(data.state)) throw new Error("Knowledge index state is invalid");
  const status: KnowledgeIndexStatus = {
    workspaceId,
    state: data.state,
    indexGeneration: requiredNullableString(data, "index_generation"),
    jobId: requiredNullableString(data, "job_id"),
    repositoryCount: requiredCount(data.repository_count),
    fileCount: requiredCount(data.file_count),
    chunkCount: requiredCount(data.chunk_count),
    updatedAt: requiredNullableString(data, "updated_at"),
    errorCode: requiredNullableString(data, "error_code"),
  };
  if ((status.state === "ready" || status.state === "stale") && !status.indexGeneration) {
    throw new Error("Knowledge index generation is invalid");
  }
  if ((status.state === "queued" || status.state === "building") && !status.jobId) {
    throw new Error("Knowledge index job is invalid");
  }
  if (status.state === "failed"
    && (!status.errorCode || !/^[a-z][a-z0-9_]{0,63}$/.test(status.errorCode))) {
    throw new Error("Knowledge index error code is invalid");
  }
  return status;
}

function record(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("Knowledge index response is invalid");
  }
  return value as Record<string, unknown>;
}

function isState(value: unknown): value is KnowledgeIndexState {
  return value === "not_indexed" || value === "queued" || value === "building"
    || value === "ready" || value === "stale" || value === "failed";
}

function requiredCount(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new Error("Knowledge index count is invalid");
  }
  return value;
}

function requiredNullableString(data: Record<string, unknown>, key: string): string | null {
  if (!Object.prototype.hasOwnProperty.call(data, key)) {
    throw new Error(`Knowledge index ${key} is invalid`);
  }
  const value = data[key];
  if (value === null) return null;
  if (nonEmptyString(value)) return value;
  throw new Error(`Knowledge index ${key} is invalid`);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}
