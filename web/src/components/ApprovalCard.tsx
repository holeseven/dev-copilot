// @author lxy
import { useState } from "react";

export type ApprovalStatus = "pending" | "submitting" | "approved" | "rejected" | "failed";

export interface Approval {
  id: string;
  action: string;
  message: string;
  repository?: string;
  virtualPath?: string;
  operation?: string;
  diff?: string;
  validationProfile?: string;
  status: ApprovalStatus;
}

export function ApprovalCard({ approval, onDecision }: {
  approval: Approval;
  onDecision: (approval: Approval, approved: boolean, reason?: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const pending = approval.status === "pending";

  return (
    <section className={`approval-card approval-${approval.status}`} aria-labelledby={`approval-${approval.id}`}>
      <header className="approval-header">
        <div>
          <span className="approval-kicker">CONTROLLED CHANGE</span>
          <h3 id={`approval-${approval.id}`}>小瑶准备修改代码</h3>
        </div>
        <span className="approval-state">{approvalStatusLabel(approval.status)}</span>
      </header>
      <p className="approval-message">{approval.message}</p>
      {(approval.repository || approval.virtualPath) ? (
        <div className="approval-target">
          <span>{approval.operation === "create" ? "新建" : "修改"}</span>
          {approval.virtualPath
            ? <code>{approval.virtualPath}</code>
            : approval.repository ? <code>{approval.repository}</code> : null}
        </div>
      ) : null}
      {approval.diff ? <DiffView diff={approval.diff} /> : (
        <p className="approval-no-diff">本次操作没有可展示的文本 Diff，请根据操作说明谨慎确认。</p>
      )}
      {approval.validationProfile ? (
        <p className="approval-validation">执行后验证：<code>{approval.validationProfile}</code></p>
      ) : null}
      <p className="approval-scope">仅批准上方 1 个变更，不会自动批准后续操作。</p>

      {rejecting && pending ? (
        <div className="rejection-form">
          <label htmlFor={`reason-${approval.id}`}>拒绝原因（可选）</label>
          <textarea id={`reason-${approval.id}`} value={reason} maxLength={1000} rows={3}
            onChange={(event) => setReason(event.target.value)} autoFocus />
          <div className="approval-actions">
            <button type="button" onClick={() => setRejecting(false)}>返回</button>
            <button className="reject-confirm" type="button"
              onClick={() => onDecision(approval, false, reason.trim() || undefined)}>确认拒绝</button>
          </div>
        </div>
      ) : pending ? (
        <div className="approval-actions">
          <button type="button" onClick={() => setRejecting(true)}>拒绝修改</button>
          <button className="approve-button" type="button"
            onClick={() => onDecision(approval, true)}>批准并执行</button>
        </div>
      ) : null}
    </section>
  );
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="diff-view" tabIndex={0} aria-label="代码变更 Diff"><code>
      {diff.split("\n").map((line, index) => (
        <span className={diffLineClass(line)} key={`${index}-${line}`}>{line || " "}{"\n"}</span>
      ))}
    </code></pre>
  );
}

function diffLineClass(line: string) {
  if (line.startsWith("+++ ") || line.startsWith("--- ")) return "diff-meta";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-remove";
  if (line.startsWith("@@")) return "diff-hunk";
  return "diff-context";
}

function approvalStatusLabel(status: ApprovalStatus) {
  return ({
    pending: "等待确认",
    submitting: "正在恢复任务…",
    approved: "已批准",
    rejected: "已拒绝",
    failed: "提交失败",
  })[status];
}
