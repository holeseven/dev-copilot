// @author lxy
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  FileChangeCard,
  VerificationCard,
  type FileChange,
  type VerificationResult,
} from "./ExecutionResults";

const fileChange: FileChange = {
  id: "proposal-1",
  toolCallId: "tool-1",
  workspaceId: "local-dev",
  virtualPath: "src/auth.py",
  operation: "replace",
  beforeSha256: "aaaaaaaaaaaaaaaa",
  afterSha256: "bbbbbbbbbbbbbbbb",
};

describe("execution result cards", () => {
  it("shows the applied file and compact integrity hashes", () => {
    render(<FileChangeCard change={fileChange} />);

    expect(screen.getByText("已应用文件")).toBeInTheDocument();
    expect(screen.getByText("src/auth.py")).toBeInTheDocument();
    expect(screen.getByText(/aaaaaaaa → bbbbbbbb/)).toBeInTheDocument();
  });

  it.each([
    ["passed", "验证通过"],
    ["failed", "验证失败"],
    ["timed_out", "验证超时"],
    ["termination_failed", "终止失败"],
    ["error", "验证异常"],
  ])("maps %s to a clear status label", (status, label) => {
    render(<VerificationCard result={{
      id: `tool-${status}`,
      toolCallId: `tool-${status}`,
      profile: "python_pytest",
      status,
      exitCode: status === "passed" ? 0 : 1,
      truncated: false,
    }} />);

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("shows truncation without exposing raw process output", () => {
    const result: VerificationResult = {
      id: "tool-1",
      toolCallId: "tool-1",
      profile: "maven_test",
      status: "failed",
      exitCode: 1,
      truncated: true,
    };
    render(<VerificationCard result={result} />);

    expect(screen.getByText(/输出达到安全上限/)).toBeInTheDocument();
    expect(screen.getByText(/仅展示验证摘要/)).toBeInTheDocument();
    expect(screen.queryByText("BUILD FAILURE")).not.toBeInTheDocument();
  });
});
