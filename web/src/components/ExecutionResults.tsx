// @author lxy
export interface FileChange {
  id: string;
  toolCallId?: string;
  workspaceId?: string;
  virtualPath: string;
  operation?: string;
  beforeSha256?: string;
  afterSha256?: string;
}

export interface VerificationResult {
  id: string;
  toolCallId?: string;
  profile: string;
  status: string;
  exitCode?: number | null;
  truncated: boolean;
}

export function FileChangeCard({ change }: { change: FileChange }) {
  const before = shortHash(change.beforeSha256);
  const after = shortHash(change.afterSha256);
  return (
    <article className="result-card file-change-card">
      <div className="result-heading">
        <span className="result-icon" aria-hidden="true">✓</span>
        <div><strong>已应用文件</strong><code>{change.virtualPath}</code></div>
        <span className="result-badge">{change.operation === "create" ? "新建" : "修改"}</span>
      </div>
      {before || after ? (
        <p className="integrity-line">内容指纹 <code>{before || "新文件"} → {after || "未知"}</code></p>
      ) : null}
    </article>
  );
}

export function VerificationCard({ result }: { result: VerificationResult }) {
  return (
    <article className={`result-card verification-card verification-${result.status}`}>
      <div className="result-heading">
        <span className="result-icon" aria-hidden="true">{verificationIcon(result.status)}</span>
        <div><strong>{verificationLabel(result.status)}</strong><code>{result.profile}</code></div>
        {typeof result.exitCode === "number" ? (
          <span className="result-badge">exit {result.exitCode}</span>
        ) : null}
      </div>
      {result.truncated ? <p className="truncation-note">输出达到安全上限，详细日志未发送到页面。</p> : null}
      <p className="empty-output">为保护本机路径与敏感信息，页面仅展示验证摘要。</p>
    </article>
  );
}

function shortHash(value?: string) {
  return value?.slice(0, 8);
}

function verificationLabel(status: string) {
  return ({
    passed: "验证通过",
    failed: "验证失败",
    timed_out: "验证超时",
    termination_failed: "终止失败",
    error: "验证异常",
  } as Record<string, string>)[status] ?? "验证进行中";
}

function verificationIcon(status: string) {
  if (status === "passed") return "✓";
  if (status === "timed_out") return "◷";
  if (status === "failed" || status === "termination_failed" || status === "error") return "!";
  return "·";
}
