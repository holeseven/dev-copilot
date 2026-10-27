// @author lxy
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { streamChat, streamResume } from "./api/chat";
import { listWorkspaces } from "./api/workspaces";

vi.mock("./api/chat", () => ({ streamChat: vi.fn(), streamResume: vi.fn() }));
vi.mock("./api/workspaces", () => ({
  listWorkspaces: vi.fn(),
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  activateWorkspace: vi.fn(),
  addRepository: vi.fn(),
  scanWorkspace: vi.fn().mockResolvedValue([]),
}));

const workspace = { id: "local-dev", name: "本地项目", repositories: [] };

describe("小瑶开发助手工作台", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listWorkspaces).mockResolvedValue([workspace]);
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
    expect(screen.getByText("验证通过")).toBeInTheDocument();
    expect(screen.getByText("python_pytest")).toBeInTheDocument();
    expect(screen.queryByText("1 passed")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /另一个项目/ }));
    await waitFor(() => expect(screen.queryByText("src/main.py")).not.toBeInTheDocument());
    expect(screen.queryByText("验证通过")).not.toBeInTheDocument();
  });
});
