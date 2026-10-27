// @author lxy
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CitationList, type Citation } from "./CitationList";
import { KnowledgeIndexCard } from "./KnowledgeIndexCard";

describe("RAG product components", () => {
  it("renders a ready index and offers a rebuild", () => {
    const rebuild = vi.fn();
    render(<KnowledgeIndexCard status={{
      workspaceId: "alpha", state: "ready", indexGeneration: "v1", jobId: null,
      repositoryCount: 2, fileCount: 8, chunkCount: 31,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    }} rebuilding={false} onRebuild={rebuild} />);

    expect(screen.getByText("知识索引可用")).toBeInTheDocument();
    expect(screen.getByText(/8 个文件 · 31 个片段/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重建知识索引" }));
    expect(rebuild).toHaveBeenCalledOnce();
  });

  it("keeps unavailable index recovery local to the card", () => {
    render(<KnowledgeIndexCard status={{
      workspaceId: "alpha", state: "unavailable", indexGeneration: null, jobId: null,
      repositoryCount: 0, fileCount: 0, chunkCount: 0, updatedAt: null,
      errorCode: "status_unavailable",
    }} rebuilding={false} onRebuild={vi.fn()} onRefresh={vi.fn()} />);

    expect(screen.getByText("知识索引状态暂不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新检查索引状态" })).toBeEnabled();
  });

  it("explains stale isolation and failed rebuild fallback accurately", () => {
    const { rerender } = render(<KnowledgeIndexCard status={{
      workspaceId: "alpha", state: "stale", indexGeneration: "old-index", jobId: null,
      repositoryCount: 1, fileCount: 8, chunkCount: 31,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    }} rebuilding={false} onRebuild={vi.fn()} />);

    expect(screen.getByText(/旧索引不可用于当前代.*重建/)).toBeInTheDocument();

    rerender(<KnowledgeIndexCard status={{
      workspaceId: "alpha", state: "failed", indexGeneration: "old-index", jobId: null,
      repositoryCount: 1, fileCount: 8, chunkCount: 31,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: "index_interrupted",
    }} rebuilding={false} onRebuild={vi.fn()} />);
    expect(screen.getByText(/服务重启中断.*上一版索引仍可继续检索/)).toBeInTheDocument();

    rerender(<KnowledgeIndexCard status={{
      workspaceId: "alpha", state: "failed", indexGeneration: null, jobId: null,
      repositoryCount: 1, fileCount: 0, chunkCount: 0,
      updatedAt: null, errorCode: "index_build_failed",
    }} rebuilding={false} onRebuild={vi.fn()} />);
    expect(screen.getByText("本次索引构建失败，请稍后重试。")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("向量");
  });

  it("renders only safe relative citation evidence", () => {
    const citations: Citation[] = [{
      id: "source-1", rank: 1, repository: "backend",
      virtualPath: "src/auth.py", startLine: 12, endLine: 18,
      excerpt: "def authenticate(user):\n    return user is not None", indexGeneration: "v1",
      contentSha256: "a".repeat(64),
    }];
    const { container } = render(<CitationList citations={citations} />);

    expect(screen.getByText("参考源码 1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("参考源码 1"));
    expect(screen.getByText("backend / src/auth.py:12–18")).toBeInTheDocument();
    expect(screen.getByText(/def authenticate/)).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/C:\\|score|repository_root/i);
  });
});
