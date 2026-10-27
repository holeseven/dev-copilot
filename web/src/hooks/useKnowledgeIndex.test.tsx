// @author lxy
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getKnowledgeIndex, rebuildKnowledgeIndex } from "../api/rag";
import { useKnowledgeIndex } from "./useKnowledgeIndex";

vi.mock("../api/rag", () => ({
  getKnowledgeIndex: vi.fn(),
  rebuildKnowledgeIndex: vi.fn(),
}));

describe("useKnowledgeIndex", () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it("ignores a late response after switching workspaces even if abort is ignored", async () => {
    let resolveAlpha!: (value: Awaited<ReturnType<typeof getKnowledgeIndex>>) => void;
    const alpha = new Promise<Awaited<ReturnType<typeof getKnowledgeIndex>>>((resolve) => {
      resolveAlpha = resolve;
    });
    vi.mocked(getKnowledgeIndex).mockImplementation((workspaceId) => workspaceId === "alpha"
      ? alpha
      : Promise.resolve(status("beta", "ready")));

    const { result, rerender } = renderHook(
      ({ workspaceId }) => useKnowledgeIndex(workspaceId, { pollIntervalMs: 10 }),
      { initialProps: { workspaceId: "alpha" } },
    );
    rerender({ workspaceId: "beta" });
    await waitFor(() => expect(result.current.status.workspaceId).toBe("beta"));

    resolveAlpha(status("alpha", "failed"));
    await act(async () => Promise.resolve());

    expect(result.current.status).toMatchObject({ workspaceId: "beta", state: "ready" });
  });

  it("polls queued work serially until it becomes ready", async () => {
    vi.useFakeTimers();
    vi.mocked(getKnowledgeIndex)
      .mockResolvedValueOnce(status("alpha", "queued"))
      .mockResolvedValueOnce(status("alpha", "building"))
      .mockResolvedValueOnce(status("alpha", "ready"));

    const { result } = renderHook(() => useKnowledgeIndex("alpha", { pollIntervalMs: 100 }));
    await act(async () => Promise.resolve());
    expect(result.current.status.state).toBe("queued");

    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(result.current.status.state).toBe("building");
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(result.current.status.state).toBe("ready");
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(getKnowledgeIndex).toHaveBeenCalledTimes(3);
  });

  it("does not let a late rebuild response replace the next workspace", async () => {
    vi.mocked(getKnowledgeIndex).mockImplementation(async (workspaceId) => status(workspaceId, "not_indexed"));
    let resolveRebuild!: (value: Awaited<ReturnType<typeof rebuildKnowledgeIndex>>) => void;
    vi.mocked(rebuildKnowledgeIndex).mockReturnValue(new Promise((resolve) => {
      resolveRebuild = resolve;
    }));
    const { result, rerender } = renderHook(
      ({ workspaceId }) => useKnowledgeIndex(workspaceId, { pollIntervalMs: 10 }),
      { initialProps: { workspaceId: "alpha" } },
    );
    await waitFor(() => expect(result.current.status.state).toBe("not_indexed"));

    let rebuildPromise!: Promise<void>;
    act(() => { rebuildPromise = result.current.rebuild(); });
    rerender({ workspaceId: "beta" });
    await waitFor(() => expect(result.current.status.workspaceId).toBe("beta"));
    resolveRebuild(status("alpha", "queued", "job-alpha"));
    await act(async () => rebuildPromise);

    expect(result.current.status).toMatchObject({ workspaceId: "beta", state: "not_indexed" });
  });
});

function status(
  workspaceId: string,
  state: "not_indexed" | "queued" | "building" | "ready" | "failed",
  jobId: string | null = null,
) {
  return {
    workspaceId,
    state,
    indexGeneration: state === "ready" ? "v1" : null,
    jobId,
    repositoryCount: 1,
    fileCount: state === "ready" ? 4 : 0,
    chunkCount: state === "ready" ? 12 : 0,
    updatedAt: state === "ready" ? "2026-10-27T10:00:00Z" : null,
    errorCode: state === "failed" ? "embedding_unavailable" : null,
  } as const;
}
