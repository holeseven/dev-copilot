// @author lxy
import { useCallback, useEffect, useRef, useState } from "react";

import {
  getKnowledgeIndex,
  rebuildKnowledgeIndex,
  type KnowledgeIndexStatus,
} from "../api/rag";

export type KnowledgeIndexViewStatus = KnowledgeIndexStatus | {
  workspaceId: string;
  state: "unavailable";
  indexGeneration: null;
  jobId: null;
  repositoryCount: 0;
  fileCount: 0;
  chunkCount: 0;
  updatedAt: null;
  errorCode: "status_unavailable";
};

interface UseKnowledgeIndexOptions {
  pollIntervalMs?: number;
}

export function useKnowledgeIndex(
  workspaceId: string,
  options: UseKnowledgeIndexOptions = {},
) {
  const pollIntervalMs = options.pollIntervalMs ?? 2000;
  const [status, setStatus] = useState<KnowledgeIndexViewStatus>(() => unavailable(workspaceId));
  const [loading, setLoading] = useState(Boolean(workspaceId));
  const [rebuilding, setRebuilding] = useState(false);
  const epochRef = useRef(0);
  const statusAbortRef = useRef<AbortController | null>(null);
  const rebuildAbortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  const load = useCallback(async (scope: string, epoch: number, showLoading: boolean) => {
    if (!scope) return;
    statusAbortRef.current?.abort();
    const controller = new AbortController();
    statusAbortRef.current = controller;
    if (showLoading) setLoading(true);
    try {
      const next = await getKnowledgeIndex(scope, { signal: controller.signal });
      if (controller.signal.aborted || epochRef.current !== epoch || next.workspaceId !== scope) return;
      setStatus(next);
      setLoading(false);
      clearTimer();
      if (next.state === "queued" || next.state === "building") {
        timerRef.current = setTimeout(() => void load(scope, epoch, false), pollIntervalMs);
      }
    } catch (error) {
      if (controller.signal.aborted || epochRef.current !== epoch || isAbort(error)) return;
      setStatus(unavailable(scope));
      setLoading(false);
      clearTimer();
    } finally {
      if (statusAbortRef.current === controller) statusAbortRef.current = null;
    }
  }, [clearTimer, pollIntervalMs]);

  useEffect(() => {
    const epoch = ++epochRef.current;
    statusAbortRef.current?.abort();
    rebuildAbortRef.current?.abort();
    clearTimer();
    setStatus(unavailable(workspaceId));
    setLoading(Boolean(workspaceId));
    setRebuilding(false);
    if (workspaceId) void load(workspaceId, epoch, true);
    return () => {
      epochRef.current += 1;
      statusAbortRef.current?.abort();
      rebuildAbortRef.current?.abort();
      clearTimer();
    };
  }, [clearTimer, load, workspaceId]);

  const refresh = useCallback(async () => {
    const epoch = epochRef.current;
    await load(workspaceId, epoch, true);
  }, [load, workspaceId]);

  const rebuild = useCallback(async () => {
    if (!workspaceId || rebuilding) return;
    const scope = workspaceId;
    const epoch = epochRef.current;
    rebuildAbortRef.current?.abort();
    const controller = new AbortController();
    rebuildAbortRef.current = controller;
    setRebuilding(true);
    try {
      const accepted = await rebuildKnowledgeIndex(scope, { signal: controller.signal });
      if (controller.signal.aborted || epochRef.current !== epoch || accepted.workspaceId !== scope) return;
      setStatus(accepted);
      clearTimer();
      timerRef.current = setTimeout(() => void load(scope, epoch, false), pollIntervalMs);
    } catch (error) {
      if (controller.signal.aborted || epochRef.current !== epoch || isAbort(error)) return;
      setStatus(unavailable(scope));
    } finally {
      if (rebuildAbortRef.current === controller) rebuildAbortRef.current = null;
      if (epochRef.current === epoch) setRebuilding(false);
    }
  }, [clearTimer, load, pollIntervalMs, rebuilding, workspaceId]);

  return { status, loading, rebuilding, refresh, rebuild };
}

function unavailable(workspaceId: string): KnowledgeIndexViewStatus {
  return {
    workspaceId,
    state: "unavailable",
    indexGeneration: null,
    jobId: null,
    repositoryCount: 0,
    fileCount: 0,
    chunkCount: 0,
    updatedAt: null,
    errorCode: "status_unavailable",
  };
}

function isAbort(error: unknown) {
  return (error instanceof DOMException && error.name === "AbortError")
    || (typeof error === "object" && error !== null && "name" in error
      && (error as { name?: unknown }).name === "AbortError");
}
