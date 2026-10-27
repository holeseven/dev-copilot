// @author lxy
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { getApprovalOutcome, getPendingApprovals, streamChat, streamResume } from "./api/chat";
import { getKnowledgeIndex, rebuildKnowledgeIndex } from "./api/rag";
import { activateWorkspace, deleteWorkspace, listWorkspaces, scanWorkspace } from "./api/workspaces";
import { loadWorkspaceSession, saveWorkspaceSession } from "./state/workspaceSessions";

vi.mock("./api/chat", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/chat")>();
  return {
    ...actual,
    getApprovalOutcome: vi.fn(),
    getPendingApprovals: vi.fn(),
    streamChat: vi.fn(),
    streamResume: vi.fn(),
  };
});
vi.mock("./api/workspaces", () => ({
  listWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  activateWorkspace: vi.fn(),
  addRepository: vi.fn(),
  scanWorkspace: vi.fn().mockResolvedValue([]),
}));
vi.mock("./api/rag", () => ({
  getKnowledgeIndex: vi.fn(), rebuildKnowledgeIndex: vi.fn(),
}));

const workspace = { id: "local-dev", name: "本地项目", repositories: [] };

describe("小瑶开发助手工作台", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listWorkspaces).mockResolvedValue([workspace]);
    vi.mocked(getKnowledgeIndex).mockResolvedValue({
      workspaceId: "local-dev", state: "not_indexed", indexGeneration: null, jobId: null,
      repositoryCount: 0, fileCount: 0, chunkCount: 0, updatedAt: null, errorCode: null,
    });
    vi.mocked(rebuildKnowledgeIndex).mockResolvedValue({
      workspaceId: "local-dev", state: "queued", indexGeneration: null, jobId: "job-1",
      repositoryCount: 0, fileCount: 0, chunkCount: 0, updatedAt: null, errorCode: null,
    });
  });

  it("shows workspace knowledge status without locking chat when status is unavailable", async () => {
    vi.mocked(getKnowledgeIndex).mockRejectedValue(new Error("offline"));
    render(<App />);

    expect(await screen.findByText("知识索引状态暂不可用")).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeEnabled();
  });

  it("binds safe citation events to the current assistant message and clears them on switch", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    vi.mocked(getKnowledgeIndex).mockImplementation(async (workspaceId) => ({
      workspaceId, state: "ready", indexGeneration: "index-1", jobId: null,
      repositoryCount: 1, fileCount: 2, chunkCount: 7,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    }));
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "message", data: { token: "认证入口在这里。[1]" } });
      await onEvent({ event: "citation", data: {
        tool_call_id: "rag-1", workspace_id: "local-dev", index_generation: "index-1",
        citation: {
          citation_id: "citation-1", rank: 1, repository: "backend",
          virtual_path: "src/auth.py", start_line: 12, end_line: 18,
          excerpt: "def authenticate(user):", score: 0.01,
          content_sha256: "a".repeat(64),
          repository_root: "C:\\secret\\repo",
        },
      } });
      await onEvent({ event: "citation", data: {
        workspace_id: "local-dev", index_generation: "index-1",
        citation: {
          citation_id: "citation-1", rank: 1, repository: "backend",
          virtual_path: "src/auth.py", start_line: 12, end_line: 18,
          excerpt: "def authenticate(user):",
          content_sha256: "a".repeat(64),
        },
      } });
      await onEvent({ event: "citation", data: {
        workspace_id: "local-dev", index_generation: "index-1",
        citation: {
          citation_id: "citation-2", rank: 2, repository: "C:\\secret\\repo",
          virtual_path: "src/leak.py", start_line: 1, end_line: 2,
          excerpt: "must not render",
          content_sha256: "a".repeat(64),
        },
      } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "认证入口在哪" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("参考源码 1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("参考源码 1"));
    expect(screen.getByText("backend / src/auth.py:12–18")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("C:\\secret\\repo");
    expect(document.body.textContent).not.toContain("0.01");

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    await waitFor(() => expect(screen.queryByText("参考源码 1")).not.toBeInTheDocument());
  });

  it("rejects stale-generation, malformed-hash, and unsafe-path citations", async () => {
    vi.mocked(getKnowledgeIndex).mockResolvedValue({
      workspaceId: "local-dev", state: "ready", indexGeneration: "index-current", jobId: null,
      repositoryCount: 1, fileCount: 2, chunkCount: 7,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    });
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      for (const [id, generation, repository, virtualPath, hash] of [
        ["old", "index-old", "backend", "src/old.py", "a".repeat(64)],
        ["hash", "index-current", "backend", "src/hash.py", "not-a-hash"],
        ["scheme", "index-current", "backend", "https://evil.test/x.py", "b".repeat(64)],
        ["slash", "index-current", "backend", "src\\leak.py", "c".repeat(64)],
        ["bidi", "index-current", "backend", "src/\u202eleak.py", "d".repeat(64)],
        ["repo", "index-current", "bad:name", "src/repo.py", "e".repeat(64)],
        ["../../unsafe", "index-current", "backend", "src/id.py", "e".repeat(64)],
        ["safe", "index-current", "backend", "src/safe.py", "f".repeat(64)],
      ] as const) {
        await onEvent({ event: "citation", data: {
          workspace_id: "local-dev", index_generation: generation,
          citation: {
            citation_id: id, rank: 1, repository, virtual_path: virtualPath,
            start_line: 1, end_line: 2, excerpt: id, content_sha256: hash,
          },
        } });
      }
      await onEvent({ event: "citation", data: {
        workspace_id: "local-dev", index_generation: "index-current",
        citation: {
          citation_id: "too-long", rank: 2, repository: "backend",
          virtual_path: "src/long.py", start_line: 1, end_line: 2,
          excerpt: "x".repeat(2001), content_sha256: "f".repeat(64),
        },
      } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    await screen.findByText("知识索引可用");
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "检查引用" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("参考源码 1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("参考源码 1"));
    expect(screen.getByText(/src\/safe\.py/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/old|hash|evil|leak|repo\.py|id\.py|long\.py/);
  });

  it("uses the latest index generation while a chat stream is active", async () => {
    vi.mocked(getKnowledgeIndex).mockResolvedValue({
      workspaceId: "local-dev", state: "ready", indexGeneration: "index-old", jobId: null,
      repositoryCount: 1, fileCount: 2, chunkCount: 7,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    });
    vi.mocked(rebuildKnowledgeIndex).mockResolvedValue({
      workspaceId: "local-dev", state: "queued", indexGeneration: "index-current", jobId: "job-2",
      repositoryCount: 1, fileCount: 2, chunkCount: 7,
      updatedAt: "2026-10-27T10:01:00Z", errorCode: null,
    });
    let emit!: Parameters<typeof streamChat>[1];
    let finish!: () => void;
    vi.mocked(streamChat).mockImplementation((_request, onEvent) => {
      emit = onEvent;
      return new Promise<void>((resolve) => { finish = resolve; });
    });
    render(<App />);
    await screen.findByText("知识索引可用");
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "持续检查引用" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: "重建知识索引" }));
    await screen.findByText("已进入索引队列");

    await act(async () => {
      for (const [id, generation, path] of [
        ["old-citation", "index-old", "src/old.py"],
        ["current-citation", "index-current", "src/current.py"],
      ]) {
        await emit({ event: "citation", data: {
          workspace_id: "local-dev", index_generation: generation,
          citation: {
            citation_id: id, rank: 1, repository: "backend", virtual_path: path,
            start_line: 1, end_line: 2, excerpt: id, content_sha256: "a".repeat(64),
          },
        } });
      }
      await emit({ event: "done", data: { status: "completed" } });
      finish();
    });

    expect(await screen.findByText("参考源码 1")).toBeInTheDocument();
    fireEvent.click(screen.getByText("参考源码 1"));
    expect(screen.getByText(/src\/current\.py/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("src/old.py");
  });

  it("replays only matching citations when index status arrives after the stream", async () => {
    let resolveIndex!: (value: Awaited<ReturnType<typeof getKnowledgeIndex>>) => void;
    vi.mocked(getKnowledgeIndex).mockImplementation(() => new Promise((resolve) => {
      resolveIndex = resolve;
    }));
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      for (const [id, generation, path] of [
        ["current", "index-current", "src/current.py"],
        ["mismatch", "index-other", "src/other.py"],
      ] as const) {
        await onEvent({ event: "citation", data: {
          workspace_id: "local-dev", index_generation: generation,
          citation: {
            citation_id: id, rank: 1, repository: "backend", virtual_path: path,
            start_line: 1, end_line: 2, excerpt: id, content_sha256: "a".repeat(64),
          },
        } });
      }
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await waitFor(() => expect(document.querySelector(".workspace-card.active")).toBeInTheDocument());
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "deferred citation" } });
    fireEvent.click(document.querySelector<HTMLButtonElement>('.composer button[type="submit"]')!);
    await waitFor(() => expect(streamChat).toHaveBeenCalledOnce());
    expect(document.querySelector(".citation-list")).not.toBeInTheDocument();

    await act(async () => resolveIndex({
      workspaceId: "local-dev", state: "ready", indexGeneration: "index-current", jobId: null,
      repositoryCount: 1, fileCount: 2, chunkCount: 7,
      updatedAt: "2026-10-27T10:00:00Z", errorCode: null,
    }));

    await waitFor(() => expect(document.querySelector(".citation-list")).toBeInTheDocument());
    fireEvent.click(document.querySelector(".citation-list summary")!);
    expect(screen.getByText(/src\/current\.py/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("src/other.py");
  });

  it.each(["queued", "building", "failed"] as const)(
    "accepts a matching active-generation citation while index state is %s",
    async (state) => {
      vi.mocked(getKnowledgeIndex).mockResolvedValue({
        workspaceId: "local-dev", state, indexGeneration: "index-active",
        jobId: state === "queued" || state === "building" ? "job-1" : null,
        repositoryCount: 1, fileCount: 2, chunkCount: 7,
        updatedAt: "2026-10-27T10:00:00Z",
        errorCode: state === "failed" ? "rebuild_failed" : null,
      });
      vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
        await onEvent({ event: "citation", data: {
          workspace_id: "local-dev", index_generation: "index-active",
          citation: {
            citation_id: `citation-${state}`, rank: 1, repository: "backend",
            virtual_path: `src/${state}.py`, start_line: 1, end_line: 2,
            excerpt: `active ${state}`, content_sha256: "a".repeat(64),
          },
        } });
        await onEvent({ event: "citation", data: {
          workspace_id: "local-dev", index_generation: "index-stale",
          citation: {
            citation_id: `stale-${state}`, rank: 2, repository: "backend",
            virtual_path: "src/stale.py", start_line: 1, end_line: 2,
            excerpt: "must not render", content_sha256: "b".repeat(64),
          },
        } });
        await onEvent({ event: "done", data: { status: "completed" } });
      });
      render(<App />);
      await screen.findByRole("button", { name: /本地项目/ });
      await waitFor(() => expect(getKnowledgeIndex).toHaveBeenCalled());
      fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
        target: { value: "检查旧索引引用" },
      });
      fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

      expect(await screen.findByText("参考源码 1")).toBeInTheDocument();
      fireEvent.click(screen.getByText("参考源码 1"));
      expect(screen.getByText(new RegExp(`src/${state}\\.py`))).toBeInTheDocument();
      expect(document.body.textContent).not.toContain("src/stale.py");
    },
  );

  it("aborts a scan, ignores its late result, and clears the draft on workspace switch", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    let scanSignal: AbortSignal | undefined;
    let resolveScan!: (value: unknown[]) => void;
    vi.mocked(scanWorkspace).mockImplementation((_workspaceId, options) => {
      if (!options) throw new Error("scan options are required");
      scanSignal = options.signal;
      return new Promise((resolve) => { resolveScan = resolve; });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "不要带到另一个工作区" },
    });
    fireEvent.click(screen.getByRole("button", { name: "重新扫描" }));
    await waitFor(() => expect(scanWorkspace).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));

    expect(scanSignal?.aborted).toBe(true);
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toHaveValue("");
    resolveScan([{ repository: "stale-repository", languages: ["Python"] }]);
    await Promise.resolve();
    expect(document.body.textContent).not.toContain("stale-repository");
  });

  it("renders the product identity and local workspace welcome", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { level: 1, name: "小瑶开发助手" })).toBeInTheDocument();
    expect(screen.getByText(/小瑶主人/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /本地项目/ })).toBeInTheDocument();
    expect(screen.getByText("代码留在本机")).toBeInTheDocument();
  });

  it("streams assistant text through the Java-only chat client", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "message", data: { token: "入口在 main.py" } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "分析入口" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("入口在 main.py")).toBeInTheDocument();
    expect(streamChat).toHaveBeenCalledOnce();
    expect(vi.mocked(streamChat).mock.calls[0][0]).toMatchObject({
      message: "分析入口",
      workspaceId: "local-dev",
    });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "继续分析" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await waitFor(() => expect(streamChat).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamChat).mock.calls[1][0].sessionId)
      .toBe(vi.mocked(streamChat).mock.calls[0][0].sessionId);
  });

  it("aborts an active stream when the user presses stop", async () => {
    let requestSignal: AbortSignal | undefined;
    vi.mocked(streamChat).mockImplementation((_request, _onEvent, options) => {
      if (!options) {
        throw new Error("chat options are required");
      }
      requestSignal = options.signal;
      return new Promise((_resolve, reject) => {
        options.signal?.addEventListener("abort", () => reject(
          new DOMException("aborted", "AbortError"),
        ));
      });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "持续分析" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    fireEvent.click(await screen.findByRole("button", { name: "停止" }));

    await waitFor(() => expect(requestSignal?.aborted).toBe(true));
    expect(await screen.findByText("已停止")).toBeInTheDocument();
  });

  it("closes the local workspace dialog with Escape", async () => {
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.click(screen.getByRole("button", { name: "添加 Workspace" }));
    expect(screen.getByRole("dialog", { name: "打开本地代码" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "打开本地代码" })).not.toBeInTheDocument();
    });
  });

  it("requires an explicit done event before reporting success", async () => {
    vi.mocked(streamChat).mockResolvedValue();
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "检查连接" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("连接在完成事件前中断，请重试本轮任务。")).toBeInTheDocument();
    expect(screen.getByText("需要处理")).toBeInTheDocument();
  });

  it("keeps errors terminal, hides upstream detail, and merges tool lifecycle rows", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "tool", data: { status: "start", tool_name: "read_file" } });
      await onEvent({ event: "tool", data: { status: "end", tool_name: "read_file" } });
      await onEvent({ event: "error", data: { code: "unknown", message: "C:\\secret\\token.txt" } });
      await onEvent({ event: "message", data: { token: "不应恢复" } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "读取文件" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("Agent 执行失败，请重试。")).toBeInTheDocument();
    expect(screen.queryByText(/secret|不应恢复/)).not.toBeInTheDocument();
    expect(screen.getAllByText("read_file")).toHaveLength(1);
    expect(screen.getByText("已完成")).toBeInTheDocument();
    expect(screen.getByText("需要处理")).toBeInTheDocument();
  });

  it("renders an inline diff approval and resumes it through the shared event handler", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-1",
        action: "write_file",
        message: "准备更新登录校验",
        payload: {
          repository: "backend",
          virtual_path: "src/auth.py",
          operation: "replace",
          diff: "@@ -1 +1 @@\n-old\n+new",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "message", data: { token: "修改已经应用" } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });

    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "修复登录" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByRole("heading", { name: "小瑶准备修改代码" })).toBeInTheDocument();
    expect(screen.getByText("src/auth.py")).toBeInTheDocument();
    expect(screen.getByText("+new", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("等待主人确认")).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    await waitFor(() => expect(streamResume).toHaveBeenCalledOnce());
    expect(vi.mocked(streamResume).mock.calls[0][0]).toMatchObject({
      sessionId: vi.mocked(streamChat).mock.calls[0][0].sessionId,
      workspaceId: "local-dev",
      approvalId: "approval-1",
      approved: true,
    });
    expect(await screen.findByText("修改已经应用")).toBeInTheDocument();
    expect(screen.getByText("本轮完成")).toBeInTheDocument();
  });

  it("submits an optional rejection reason only once", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-2", action: "write_file", message: "修改配置", payload: {},
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockReturnValue(new Promise(() => undefined));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "修改配置" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await screen.findByRole("button", { name: "拒绝修改" });

    fireEvent.click(screen.getByRole("button", { name: "拒绝修改" }));
    fireEvent.change(screen.getByLabelText("拒绝原因（可选）"), {
      target: { value: "需要先补测试" },
    });
    const confirm = screen.getByRole("button", { name: "确认拒绝" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);

    await waitFor(() => expect(streamResume).toHaveBeenCalledOnce());
    expect(vi.mocked(streamResume).mock.calls[0][0]).toMatchObject({
      approvalId: "approval-2",
      approved: false,
      reason: "需要先补测试",
    });
    expect(screen.getByText("正在恢复任务…")).toBeInTheDocument();
  });

  it.each([
    ["applied_and_tests_passed", "passed", "已应用并验证", "验证通过"],
    ["applied_tests_failed", "failed", "已应用，验证未通过", "验证失败"],
  ] as const)("renders a normal resume SSE outcome for %s without generic approval overwrite", async (
    outcomeStatus, validationStatus, approvalLabel, verificationLabel,
  ) => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-sse-outcome", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/sse-outcome.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockImplementation(async (request, onEvent) => {
      await onEvent({ event: "outcome", data: {
        workspace_id: request.workspaceId, session_id: request.sessionId,
        approval_id: request.approvalId, status: outcomeStatus, terminal: true,
        mutation: {
          proposal_id: request.approvalId, workspace_id: request.workspaceId,
          virtual_path: "src/sse-outcome.py", operation: "replace",
          before_sha256: "a".repeat(64), after_sha256: "b".repeat(64),
        },
        validation: {
          profile: "python_pytest", status: validationStatus,
          exit_code: validationStatus === "passed" ? 0 : 1, truncated: false,
        },
      } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "正常 outcome" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    fireEvent.click(await screen.findByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText(approvalLabel)).toBeInTheDocument();
    expect(screen.getByText(verificationLabel)).toBeInTheDocument();
    expect(getApprovalOutcome).not.toHaveBeenCalled();
    expect(screen.queryByText("已批准")).not.toBeInTheDocument();
  });

  it("fails closed for a normal resume SSE outcome with malicious identity", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-sse-evil", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/safe-sse.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockImplementation(async (request, onEvent) => {
      await onEvent({ event: "outcome", data: {
        workspace_id: "evil", session_id: request.sessionId,
        approval_id: request.approvalId, status: "applied_recovered", terminal: true,
        mutation: {
          proposal_id: request.approvalId, workspace_id: "evil",
          virtual_path: "src/evil-sse.py", operation: "replace",
          before_sha256: null, after_sha256: "e".repeat(64),
        },
      } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    vi.mocked(getApprovalOutcome).mockRejectedValue(new Error("outcome unavailable"));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "恶意 SSE outcome" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    fireEvent.click(await screen.findByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText("无法确认执行结果")).toBeInTheDocument();
    expect(screen.queryByText("src/evil-sse.py")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准并执行" })).not.toBeInTheDocument();
  });

  it("recovers an applying outcome after the approval stream disconnects", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-applying", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/app.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockRejectedValue(new Error("stream disconnected"));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "修改应用" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await screen.findByRole("button", { name: "批准并执行" });
    const sessionId = vi.mocked(streamChat).mock.calls[0][0].sessionId;
    vi.mocked(getApprovalOutcome).mockImplementation(async (request) => ({
      workspace_id: request.workspaceId, session_id: sessionId,
      approval_id: request.approvalId, status: "applying", terminal: false,
    }));

    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText("正在执行")).toBeInTheDocument();
    expect(screen.getAllByText("小瑶正在执行，断线后仍会继续")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "批准并执行" })).not.toBeInTheDocument();
    expect(getApprovalOutcome).toHaveBeenCalledWith(expect.objectContaining({
      approvalId: "approval-applying", sessionId,
    }), expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it.each([
    ["applied_and_tests_passed", "passed", "已应用并验证", "验证通过"],
    ["applied_tests_failed", "failed", "已应用，验证未通过", "验证失败"],
  ] as const)("renders recovered %s outcome in the existing result ledger", async (
    outcomeStatus, validationStatus, approvalLabel, verificationLabel,
  ) => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-result", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/result.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockRejectedValue(new Error("stream disconnected"));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "修改结果" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await screen.findByRole("button", { name: "批准并执行" });
    const sessionId = vi.mocked(streamChat).mock.calls[0][0].sessionId;
    vi.mocked(getApprovalOutcome).mockResolvedValue({
      workspace_id: "local-dev", session_id: sessionId,
      approval_id: "approval-result", status: outcomeStatus, terminal: true,
      mutation: {
        proposal_id: "approval-result", workspace_id: "local-dev",
        virtual_path: "src/result.py", operation: "replace",
        before_sha256: "a".repeat(64), after_sha256: "b".repeat(64),
      },
      validation: {
        profile: "python_pytest", status: validationStatus,
        exit_code: validationStatus === "passed" ? 0 : 1, truncated: false,
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText(approvalLabel)).toBeInTheDocument();
    expect(screen.getAllByText("src/result.py").length).toBeGreaterThan(0);
    expect(screen.getByText(verificationLabel)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准并执行" })).not.toBeInTheDocument();
  });

  it("uses the authoritative outcome after an explicit resume stream failure", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-stream-error", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/stream-error.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "error", data: { code: "upstream_unavailable" } });
      await onEvent({ event: "done", data: { status: "failed" } });
    });
    vi.mocked(getApprovalOutcome).mockImplementation(async (request) => ({
      workspace_id: request.workspaceId, session_id: request.sessionId,
      approval_id: request.approvalId, status: "applied_recovered", terminal: true,
      mutation: {
        proposal_id: request.approvalId, workspace_id: request.workspaceId,
        virtual_path: "src/stream-error.py", operation: "replace",
        before_sha256: "a".repeat(64), after_sha256: "b".repeat(64),
      },
    }));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "处理流失败" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    fireEvent.click(await screen.findByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText("已应用")).toBeInTheDocument();
    expect(screen.getAllByText("src/stream-error.py").length).toBeGreaterThan(0);
    expect(getApprovalOutcome).toHaveBeenCalledOnce();
  });

  it("keeps an approval locked and recovers its outcome when Stop interrupts resume", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-stop", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/stop.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockReturnValue(new Promise(() => undefined));
    vi.mocked(getApprovalOutcome).mockImplementation(async (request) => ({
      workspace_id: request.workspaceId, session_id: request.sessionId,
      approval_id: request.approvalId, status: "applying", terminal: false,
    }));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "停止恢复" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    fireEvent.click(await screen.findByRole("button", { name: "批准并执行" }));
    fireEvent.click(await screen.findByRole("button", { name: "停止" }));

    expect(await screen.findByText("正在执行")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准并执行" })).not.toBeInTheDocument();
    expect(getApprovalOutcome).toHaveBeenCalledOnce();
  });

  it("restores terminal pending outcomes on page load", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev", session_id: "persisted-session",
      status: "idle", approvals: [],
      outcomes: [{
        workspace_id: "local-dev", session_id: "persisted-session",
        approval_id: "approval-restored", status: "applied_recovered", terminal: true,
        mutation: {
          proposal_id: "approval-restored", workspace_id: "local-dev",
          virtual_path: "src/restored-result.py", operation: "create",
          before_sha256: null, after_sha256: "c".repeat(64),
        },
      }],
    });

    render(<App />);

    expect((await screen.findAllByText("src/restored-result.py")).length).toBeGreaterThan(0);
    expect(screen.getByText("已应用")).toBeInTheDocument();
  });

  it("continues recovering a non-terminal pending outcome after refresh", async () => {
    saveWorkspaceSession("local-dev", "persisted-applying-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev", session_id: "persisted-applying-session",
      status: "idle", approvals: [],
      outcomes: [{
        workspace_id: "local-dev", session_id: "persisted-applying-session",
        approval_id: "approval-refresh-applying", status: "applying", terminal: false,
      }],
    });
    vi.mocked(getApprovalOutcome).mockResolvedValue({
      workspace_id: "local-dev", session_id: "persisted-applying-session",
      approval_id: "approval-refresh-applying", status: "applied_recovered", terminal: true,
      mutation: {
        proposal_id: "approval-refresh-applying", workspace_id: "local-dev",
        virtual_path: "src/refreshed.py", operation: "replace",
        before_sha256: "d".repeat(64), after_sha256: "e".repeat(64),
      },
    });

    render(<App />);

    expect((await screen.findAllByText("src/refreshed.py")).length).toBeGreaterThan(0);
    expect(getApprovalOutcome).toHaveBeenCalledWith({
      workspaceId: "local-dev",
      sessionId: "persisted-applying-session",
      approvalId: "approval-refresh-applying",
    }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("aborts and ignores a stale approval outcome after switching workspaces", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace, { id: "other", name: "另一个项目", repositories: [] },
    ]);
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-stale", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/stale.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockRejectedValue(new Error("stream disconnected"));
    let resolveOutcome!: (value: Awaited<ReturnType<typeof getApprovalOutcome>>) => void;
    let outcomeSignal: AbortSignal | undefined;
    vi.mocked(getApprovalOutcome).mockImplementation((_request, options) => {
      outcomeSignal = options?.signal;
      return new Promise((resolve) => { resolveOutcome = resolve; });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "修改后切换" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await screen.findByRole("button", { name: "批准并执行" });
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));
    await waitFor(() => expect(getApprovalOutcome).toHaveBeenCalledOnce());

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    expect(outcomeSignal?.aborted).toBe(true);
    resolveOutcome({
      workspace_id: "local-dev", session_id: "stale-session",
      approval_id: "approval-stale", status: "applied_recovered", terminal: true,
      mutation: {
        proposal_id: "approval-stale", workspace_id: "local-dev",
        virtual_path: "src/must-not-render.py", operation: "replace",
        before_sha256: null, after_sha256: "d".repeat(64),
      },
    });

    await waitFor(() => expect(screen.queryByText("src/must-not-render.py")).not.toBeInTheDocument());
  });

  it("fails closed when a mocked outcome identity does not match the active workspace", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "approval_required", data: {
        approval_id: "approval-evil", action: "apply_workspace_change",
        message: "应用受控修改", payload: {
          virtual_path: "src/safe.py", diff: "+safe", operation: "replace",
          validation_profile: "python_pytest",
        },
      } });
      await onEvent({ event: "done", data: { status: "awaiting_approval" } });
    });
    vi.mocked(streamResume).mockRejectedValue(new Error("stream disconnected"));
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText(/告诉小瑶/), { target: { value: "恶意响应" } });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));
    await screen.findByRole("button", { name: "批准并执行" });
    const sessionId = vi.mocked(streamChat).mock.calls[0][0].sessionId;
    vi.mocked(getApprovalOutcome).mockResolvedValue({
      workspace_id: "evil", session_id: sessionId, approval_id: "approval-evil",
      status: "applied_recovered", terminal: true,
      mutation: {
        proposal_id: "approval-evil", workspace_id: "evil",
        virtual_path: "src/evil.py", operation: "replace",
        before_sha256: null, after_sha256: "e".repeat(64),
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));

    expect(await screen.findByText("无法确认执行结果")).toBeInTheDocument();
    expect(screen.queryByText("src/evil.py")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "批准并执行" })).not.toBeInTheDocument();
  });

  it("merges concurrent tool events by tool_call_id instead of tool name", async () => {
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "tool", data: { status: "start", tool_name: "read_file", tool_call_id: "a" } });
      await onEvent({ event: "tool", data: { status: "start", tool_name: "read_file", tool_call_id: "b" } });
      await onEvent({ event: "tool", data: { status: "end", tool_name: "read_file", tool_call_id: "a" } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "并发读取" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    await waitFor(() => expect(screen.getAllByText("read_file")).toHaveLength(2));
    expect(screen.getAllByText("已完成")).toHaveLength(1);
    expect(screen.getAllByText("执行中")).toHaveLength(1);
  });

  it("stores file changes and verification results and clears them on workspace switch", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    vi.mocked(streamChat).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "file_change", data: {
        tool_call_id: "tool-1",
        proposal_id: "proposal-1",
        workspace_id: "local-dev",
        virtual_path: "src/main.py",
        operation: "replace",
        before_sha256: "aaaaaaaaaaaaaaaa",
        after_sha256: "bbbbbbbbbbbbbbbb",
      } });
      await onEvent({ event: "verification", data: {
        tool_call_id: "tool-1",
        profile: "python_pytest",
        status: "passed",
        exit_code: 0,
        stdout: "1 passed",
        stderr: "",
        truncated: false,
      } });
      await onEvent({ event: "done", data: { status: "completed" } });
    });
    render(<App />);
    await screen.findByRole("button", { name: /本地项目/ });
    fireEvent.change(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么"), {
      target: { value: "修改并验证" },
    });
    fireEvent.click(screen.getByRole("button", { name: /开始协作/ }));

    expect(await screen.findByText("src/main.py")).toBeInTheDocument();
    expect(await screen.findByText("验证通过")).toBeInTheDocument();
    expect(screen.getByText("python_pytest")).toBeInTheDocument();
    expect(screen.queryByText("1 passed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    await waitFor(() => expect(screen.queryByText("src/main.py")).not.toBeInTheDocument());
    expect(screen.queryByText("验证通过")).not.toBeInTheDocument();
  });

  it("restores a pending approval on load and resumes with the persisted session", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "awaiting_approval",
      approvals: [{
        approval_id: "restored-approval",
        action: "apply_workspace_change",
        message: "恢复待审批修改",
        payload: {
          virtual_path: "src/restored.py", diff: "+restored", operation: "replace",
          validation_profile: "python_pytest",
        },
      }],
    });
    vi.mocked(streamResume).mockImplementation(async (_request, onEvent) => {
      await onEvent({ event: "done", data: { status: "completed" } });
    });

    render(<App />);

    expect(await screen.findByText("src/restored.py")).toBeInTheDocument();
    expect(screen.getByText("等待主人确认")).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "批准并执行" }));
    await waitFor(() => expect(streamResume).toHaveBeenCalledOnce());
    expect(vi.mocked(streamResume).mock.calls[0][0]).toMatchObject({
      workspaceId: "local-dev",
      sessionId: "persisted-session",
      approvalId: "restored-approval",
    });
  });

  it("keeps a current pending approval authoritative over historical terminal outcomes", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "awaiting_approval",
      approvals: [{
        approval_id: "current-approval",
        action: "apply_workspace_change",
        message: "当前待审批修改",
        payload: {
          virtual_path: "src/current.py", diff: "+current", operation: "replace",
          validation_profile: "python_pytest",
        },
      }],
      outcomes: [{
        workspace_id: "local-dev", session_id: "persisted-session",
        approval_id: "historical-outcome", status: "applied_recovered", terminal: true,
        mutation: {
          proposal_id: "historical-outcome", workspace_id: "local-dev",
          virtual_path: "src/historical.py", operation: "replace",
          before_sha256: "a".repeat(64), after_sha256: "b".repeat(64),
        },
      }],
    });

    render(<App />);

    expect(await screen.findByText("src/current.py")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "批准并执行" })).toBeInTheDocument();
    expect(screen.getByText("等待主人确认")).toBeInTheDocument();
    expect(screen.queryByText("本轮完成")).not.toBeInTheDocument();
    expect(screen.getAllByText("src/historical.py").length).toBeGreaterThan(0);
  });

  it("fails closed when recovery fails and can retry", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({
        workspace_id: "local-dev",
        session_id: "persisted-session",
        status: "awaiting_approval",
        approvals: [{
          approval_id: "approval-after-retry",
          action: "apply_workspace_change",
          message: "重试后恢复",
          payload: {
            virtual_path: "retry.py", diff: "+retry", operation: "create",
            validation_profile: "python_pytest",
          },
        }],
      });
    render(<App />);

    const retry = await screen.findByRole("button", { name: "重新检查待审批任务" });
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();
    expect(screen.getByRole("button", { name: /梳理项目入口和模块关系/ })).toBeDisabled();
    fireEvent.click(retry);

    expect(await screen.findByText("retry.py")).toBeInTheDocument();
    expect(getPendingApprovals).toHaveBeenCalledTimes(2);
  });

  it("ignores a stale recovery response after switching workspaces", async () => {
    saveWorkspaceSession("local-dev", "session-local");
    saveWorkspaceSession("other", "session-other");
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    let resolveFirst!: (value: Awaited<ReturnType<typeof getPendingApprovals>>) => void;
    let firstSignal: AbortSignal | undefined;
    const first = new Promise<Awaited<ReturnType<typeof getPendingApprovals>>>((resolve) => {
      resolveFirst = resolve;
    });
    vi.mocked(getPendingApprovals).mockImplementation((request, options) => {
      if (request.workspaceId === "local-dev") {
        firstSignal = options?.signal;
        return first;
      }
      return Promise.resolve({
        workspace_id: "other",
        session_id: "session-other",
        status: "awaiting_approval",
        approvals: [{
          approval_id: "approval-other",
          action: "apply_workspace_change",
          message: "另一个项目审批",
          payload: {
            virtual_path: "other.py", diff: "+other", operation: "create",
            validation_profile: "python_pytest",
          },
        }],
      });
    });
    render(<App />);
    await screen.findByRole("button", { name: /另一个项目/ });

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    expect(await screen.findByText("other.py")).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    resolveFirst({
      workspace_id: "local-dev",
      session_id: "session-local",
      status: "awaiting_approval",
      approvals: [{
        approval_id: "stale-approval",
        action: "apply_workspace_change",
        message: "旧响应",
        payload: {
          virtual_path: "stale.py", diff: "+stale", operation: "create",
          validation_profile: "python_pytest",
        },
      }],
    });

    await waitFor(() => expect(screen.queryByText("stale.py")).not.toBeInTheDocument());
    expect(screen.getByText("other.py")).toBeInTheDocument();
  });

  it("fails closed for an unknown recovery status", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "unexpected" as "idle",
      approvals: [],
    });

    render(<App />);

    expect(await screen.findByRole("button", { name: "重新检查待审批任务" })).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();
  });

  it("fails closed when idle status contains pending approvals", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "idle",
      approvals: [{
        approval_id: "contradictory-approval",
        action: "apply_workspace_change",
        message: "不一致审批",
        payload: {
          virtual_path: "unsafe.py", diff: "+unsafe", operation: "create",
          validation_profile: "python_pytest",
        },
      }],
    });

    render(<App />);

    expect(await screen.findByRole("button", { name: "重新检查待审批任务" })).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();
    expect(screen.queryByText("unsafe.py")).not.toBeInTheDocument();
  });

  it.each([
    ["wrong action", { action: "write_file" }],
    ["missing virtual path", { payload: {
      diff: "+safe", operation: "create", validation_profile: "python_pytest",
    } }],
    ["missing diff", { payload: {
      virtual_path: "safe.py", operation: "create", validation_profile: "python_pytest",
    } }],
    ["unsupported operation", { payload: {
      virtual_path: "safe.py", diff: "+safe", operation: "delete",
      validation_profile: "python_pytest",
    } }],
    ["missing validation profile", { payload: {
      virtual_path: "safe.py", diff: "+safe", operation: "create",
    } }],
  ])("fails closed for a recovered approval with %s", async (_label, override) => {
    saveWorkspaceSession("local-dev", "persisted-session");
    const base = {
      approval_id: "approval-1",
      action: "apply_workspace_change",
      message: "严格校验",
      payload: {
        virtual_path: "safe.py", diff: "+safe", operation: "create",
        validation_profile: "python_pytest",
      },
    };
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "awaiting_approval",
      approvals: [{ ...base, ...override }],
    });

    render(<App />);

    expect(await screen.findByRole("button", { name: "重新检查待审批任务" })).toBeInTheDocument();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeDisabled();
  });

  it("clears workspace state and persisted session after deletion", async () => {
    saveWorkspaceSession("local-dev", "persisted-session");
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    vi.mocked(getPendingApprovals).mockResolvedValue({
      workspace_id: "local-dev",
      session_id: "persisted-session",
      status: "awaiting_approval",
      approvals: [{
        approval_id: "approval-to-remove",
        action: "apply_workspace_change",
        message: "待删除",
        payload: {
          virtual_path: "removed.py", diff: "+removed", operation: "create",
          validation_profile: "python_pytest",
        },
      }],
    });
    vi.mocked(deleteWorkspace).mockResolvedValue();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<App />);
    expect(await screen.findByText("removed.py")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "移除" }));

    await waitFor(() => expect(deleteWorkspace).toHaveBeenCalledWith("local-dev"));
    await waitFor(() => expect(screen.queryByText("removed.py")).not.toBeInTheDocument());
    expect(loadWorkspaceSession("local-dev")).toBeUndefined();
    expect(screen.getByLabelText("告诉小瑶你想分析、修改或验证什么")).toBeEnabled();
  });

  it("aborts and ignores a late workspace activation result", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue([
      workspace,
      { id: "other", name: "另一个项目", repositories: [] },
    ]);
    let rejectOther!: (reason?: unknown) => void;
    let otherSignal: AbortSignal | undefined;
    vi.mocked(activateWorkspace).mockImplementation((id, options) => {
      if (id === "other") {
        otherSignal = options?.signal;
        return new Promise((_resolve, reject) => { rejectOther = reject; });
      }
      return Promise.resolve(workspace);
    });
    render(<App />);
    await screen.findByRole("button", { name: /另一个项目/ });

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    fireEvent.click(await screen.findByRole("button", { name: /本地项目/ }));
    await waitFor(() => expect(otherSignal?.aborted).toBe(true));
    rejectOther(new Error("late failure"));

    await waitFor(() => expect(screen.queryByText("Workspace 切换失败，请重试。")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /本地项目/ })).toHaveAttribute("aria-current", "true");
  });
});
