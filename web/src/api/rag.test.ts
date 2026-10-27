// @author lxy
import { afterEach, describe, expect, it, vi } from "vitest";

import { getKnowledgeIndex, rebuildKnowledgeIndex } from "./rag";

describe("RAG API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the workspace-scoped index routes and forwards AbortSignal", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse({
        workspace_id: "team/blue space", state: "ready", index_generation: "v1",
        job_id: null, repository_count: 2, file_count: 12, chunk_count: 36,
        updated_at: "2026-10-27T10:00:00Z", error_code: null,
      }))
      .mockResolvedValueOnce(jsonResponse({
        workspace_id: "team/blue space", state: "queued", index_generation: "v1",
        job_id: "job-1", repository_count: 2, file_count: 12, chunk_count: 36,
        updated_at: "2026-10-27T10:00:00Z", error_code: null,
      }, 202));
    vi.stubGlobal("fetch", fetchMock);

    const current = await getKnowledgeIndex("team/blue space", { signal });
    const accepted = await rebuildKnowledgeIndex("team/blue space", { signal });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/workspaces/team%2Fblue%20space/rag/index",
      "/api/v1/workspaces/team%2Fblue%20space/rag/index/rebuild",
    ]);
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "GET", signal });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "POST", signal });
    expect(current).toMatchObject({
      workspaceId: "team/blue space", state: "ready", indexGeneration: "v1",
      repositoryCount: 2, fileCount: 12, chunkCount: 36, errorCode: null,
    });
    expect(accepted).toMatchObject({
      workspaceId: "team/blue space", state: "queued", indexGeneration: "v1",
      jobId: "job-1", repositoryCount: 2, fileCount: 12, chunkCount: 36,
    });
  });

  it("fails closed for an unknown state or mismatched workspace", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(jsonResponse({ workspace_id: "alpha", state: "mystery" }))
      .mockResolvedValueOnce(jsonResponse({ workspace_id: "beta", state: "not_indexed" })));

    await expect(getKnowledgeIndex("alpha")).rejects.toThrow("invalid");
    await expect(getKnowledgeIndex("alpha")).rejects.toThrow("workspace");
  });

  it("requires rebuild acceptance with HTTP 202", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      workspace_id: "alpha", state: "queued", index_generation: null,
      job_id: "job-1", repository_count: 1, file_count: 0, chunk_count: 0,
      updated_at: null, error_code: null,
    }, 200)));

    await expect(rebuildKnowledgeIndex("alpha")).rejects.toThrow("202");
  });

  it.each([
    ["missing index_generation", { job_id: null, updated_at: null, error_code: null }],
    ["wrong job_id type", { index_generation: null, job_id: 7, updated_at: null, error_code: null }],
    ["missing updated_at", { index_generation: null, job_id: null, error_code: null }],
    ["wrong error_code type", { index_generation: null, job_id: null, updated_at: null, error_code: false }],
  ])("fails closed for %s", async (_label, nullableFields) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      workspace_id: "alpha", state: "not_indexed",
      repository_count: 0, file_count: 0, chunk_count: 0,
      ...nullableFields,
    })));

    await expect(getKnowledgeIndex("alpha")).rejects.toThrow("invalid");
  });

  it.each([
    ["ready without generation", "ready", null, null, null],
    ["stale without generation", "stale", null, null, null],
    ["queued without job", "queued", "index-1", null, null],
    ["building without job", "building", "index-1", null, null],
    ["failed without error code", "failed", "index-1", null, null],
    ["failed with unsafe error code", "failed", "index-1", null, "C:/secret"],
  ])("fails closed for %s", async (_label, state, indexGeneration, jobId, errorCode) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({
      workspace_id: "alpha", state, index_generation: indexGeneration,
      job_id: jobId, repository_count: 1, file_count: 2, chunk_count: 3,
      updated_at: null, error_code: errorCode,
    })));

    await expect(getKnowledgeIndex("alpha")).rejects.toThrow("invalid");
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
