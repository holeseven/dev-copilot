// @author lxy
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { streamChat } from "./api/chat";
import { listWorkspaces } from "./api/workspaces";

vi.mock("./api/chat", () => ({ streamChat: vi.fn() }));
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
});
