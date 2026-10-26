// @author lxy

const WORKSPACES_URL = "/api/v1/workspaces";

export interface Workspace {
  id: string;
  name: string;
  active?: boolean;
  repositories?: WorkspaceRepository[];
  [key: string]: unknown;
}

export interface WorkspaceRepository {
  name: string;
  path: string;
  [key: string]: unknown;
}

export interface CreateWorkspaceRequest {
  id: string;
  name: string;
}

export interface AddRepositoryRequest {
  name: string;
  localPath: string;
}

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

export class WorkspaceApiError extends Error {
  constructor(public readonly status: number) {
    super(`Workspace request failed with status ${status}`);
    this.name = "WorkspaceApiError";
  }
}

export function listWorkspaces(
  options: ApiRequestOptions = {},
): Promise<Workspace[]> {
  return requestJson<Workspace[]>(WORKSPACES_URL, {
    method: "GET",
    signal: options.signal,
  });
}

export function createWorkspace(
  workspace: CreateWorkspaceRequest,
  options: ApiRequestOptions = {},
): Promise<Workspace> {
  return requestJson<Workspace>(WORKSPACES_URL, jsonRequest("POST", workspace, options));
}

export function getWorkspace(
  workspaceId: string,
  options: ApiRequestOptions = {},
): Promise<Workspace> {
  return requestJson<Workspace>(workspaceUrl(workspaceId), {
    method: "GET",
    signal: options.signal,
  });
}

export function deleteWorkspace(
  workspaceId: string,
  options: ApiRequestOptions = {},
): Promise<void> {
  return requestJson<void>(workspaceUrl(workspaceId), {
    method: "DELETE",
    signal: options.signal,
  });
}

export function addRepository(
  workspaceId: string,
  repository: AddRepositoryRequest,
  options: ApiRequestOptions = {},
): Promise<Workspace> {
  return requestJson<Workspace>(
    `${workspaceUrl(workspaceId)}/repositories`,
    jsonRequest("POST", {
      name: repository.name,
      local_path: repository.localPath,
    }, options),
  );
}

export function scanWorkspace(
  workspaceId: string,
  options: ApiRequestOptions = {},
): Promise<unknown[]> {
  return requestJson<unknown[]>(`${workspaceUrl(workspaceId)}/scan`, {
    method: "POST",
    signal: options.signal,
  });
}

export function activateWorkspace(
  workspaceId: string,
  options: ApiRequestOptions = {},
): Promise<Workspace> {
  return requestJson<Workspace>(`${workspaceUrl(workspaceId)}/activate`, {
    method: "POST",
    signal: options.signal,
  });
}

function workspaceUrl(workspaceId: string): string {
  return `${WORKSPACES_URL}/${encodeURIComponent(workspaceId)}`;
}

function jsonRequest(
  method: string,
  body: unknown,
  options: ApiRequestOptions,
): RequestInit {
  return {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal: options.signal,
  };
}

async function requestJson<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new WorkspaceApiError(response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return await response.json() as T;
}
