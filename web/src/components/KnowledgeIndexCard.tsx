// @author lxy
import type { KnowledgeIndexViewStatus } from "../hooks/useKnowledgeIndex";

export function KnowledgeIndexCard({ status, loading, rebuilding, onRebuild, onRefresh }: {
  status: KnowledgeIndexViewStatus;
  loading?: boolean;
  rebuilding: boolean;
  onRebuild: () => void;
  onRefresh?: () => void;
}) {
  if (loading) {
    return <section className="knowledge-card knowledge-loading" aria-label="知识索引" aria-busy="true">
      <span className="knowledge-orbit" aria-hidden="true" />
      <div><strong>正在读取知识索引</strong><small>确认当前 Workspace 的检索状态…</small></div>
    </section>;
  }

  const copy = stateCopy(status);
  const active = status.state === "queued" || status.state === "building" || rebuilding;
  return <section className={`knowledge-card knowledge-${status.state}`} aria-label="知识索引" aria-busy={active}>
    <div className="knowledge-heading">
      <span className="knowledge-orbit" aria-hidden="true" />
      <div><small>LOCAL KNOWLEDGE</small><strong>{copy.title}</strong></div>
      <span className="knowledge-state" role="status">{copy.badge}</span>
    </div>
    <p>{copy.description}</p>
    {(status.state === "ready" || status.state === "stale") ? <p className="knowledge-metrics">
      {status.fileCount} 个文件 · {status.chunkCount} 个片段
      {status.updatedAt ? <time dateTime={status.updatedAt}> · {formatTime(status.updatedAt)}</time> : null}
    </p> : null}
    <div className="knowledge-actions">
      {status.state === "unavailable" ? <button type="button" onClick={onRefresh}>
        重新检查索引状态
      </button> : active ? <button type="button" disabled>索引构建中</button> : <button
        type="button" onClick={onRebuild} aria-label={status.state === "not_indexed" ? "建立知识索引" : "重建知识索引"}>
        {status.state === "not_indexed" ? "建立索引" : "重建索引"}
      </button>}
    </div>
  </section>;
}

function stateCopy(status: KnowledgeIndexViewStatus) {
  switch (status.state) {
    case "not_indexed": return { title: "尚未建立知识索引", badge: "EMPTY", description: "建立后，小瑶能从项目文档中检索证据。" };
    case "queued": return { title: "已进入索引队列", badge: "QUEUED", description: status.indexGeneration ? "正在更新，当前仍使用上一版索引。" : "首次构建中，知识检索暂不可用。" };
    case "building": return { title: "正在理解项目", badge: "BUILDING", description: status.indexGeneration ? "正在更新，当前仍使用上一版索引。" : "首次构建中，仍可使用文件工具。" };
    case "ready": return { title: "知识索引可用", badge: "READY", description: "回答可附带可核查的项目来源。" };
    case "stale": return { title: "代码有变化", badge: "STALE", description: "当前 Workspace 代际已变化，旧索引不可用于当前代，请重建索引。" };
    case "failed": return {
      title: "索引建立失败",
      badge: "FAILED",
      description: `${safeErrorMessage(status.errorCode)}${status.indexGeneration ? " 上一版索引仍可继续检索。" : ""}`,
    };
    case "unavailable": return { title: "知识索引状态暂不可用", badge: "OFFLINE", description: "聊天和文件工具仍可继续使用。" };
  }
}

function safeErrorMessage(errorCode: string | null) {
  if (errorCode === "index_interrupted") return "索引任务因服务重启中断。";
  if (errorCode === "index_build_failed") return "本次索引构建失败，请稍后重试。";
  return "暂时无法建立索引，请稍后重试。";
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "更新时间未知" : new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}
