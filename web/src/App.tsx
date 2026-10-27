// @author lxy
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { streamChat, streamResume } from "./api/chat";
import type { AgentEvent } from "./api/sse";
import { ApprovalCard, type Approval } from "./components/ApprovalCard";
import {
  FileChangeCard,
  VerificationCard,
  type FileChange,
  type VerificationResult,
} from "./components/ExecutionResults";
import {
  activateWorkspace,
  addRepository,
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
  scanWorkspace,
  type Workspace,
} from "./api/workspaces";

type RunStatus = "idle" | "connecting" | "streaming" | "awaiting_approval" | "resuming" | "completed" | "failed" | "cancelled";
type Message = { id: string; role: "user" | "assistant"; content: string };
type ToolActivity = { id: string; name: string; status: string };
type TerminalStatus = "completed" | "failed" | "awaiting_approval";
type ScanSummary = {
  repository?: string;
  languages?: string[];
  build_systems?: string[];
  services?: string[];
};

const QUICK_PROMPTS = [
  "梳理项目入口和模块关系",
  "找出最值得优先修复的风险",
  "解释这个项目如何本地启动",
];

export function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [workspaceOpen, setWorkspaceOpen] = useState(() => !matchesMedia("(max-width: 900px)"));
  const [createOpen, setCreateOpen] = useState(false);
  const [loadingWorkspaces, setLoadingWorkspaces] = useState(true);
  const [scan, setScan] = useState<ScanSummary[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [tools, setTools] = useState<ToolActivity[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [fileChanges, setFileChanges] = useState<FileChange[]>([]);
  const [verifications, setVerifications] = useState<VerificationResult[]>([]);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<RunStatus>("idle");
  const [notice, setNotice] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const terminalRef = useRef<{ generation: number; status: TerminalStatus | null }>({
    generation: 0,
    status: null,
  });
  const sessionIdsRef = useRef(new Map<string, string>());
  const approvalSubmissionsRef = useRef(new Set<string>());
  const compactLayout = useMediaQuery("(max-width: 900px)");

  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.id === workspaceId),
    [workspaceId, workspaces],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refreshWorkspaces(controller.signal);
    return () => {
      controller.abort();
      abortRef.current?.abort();
    };
  }, []);

  async function refreshWorkspaces(signal?: AbortSignal) {
    try {
      const items = await listWorkspaces({ signal });
      setWorkspaces(items);
      setWorkspaceId((current) => current || items[0]?.id || "");
      setNotice("");
    } catch (error) {
      if (!isAbort(error)) setNotice("暂时无法读取 Workspace，请确认 Java 服务已经启动。");
    } finally {
      setLoadingWorkspaces(false);
    }
  }

  async function chooseWorkspace(nextId: string) {
    if (nextId === workspaceId) return;
    stopRun("cancelled");
    setWorkspaceId(nextId);
    setMessages([]);
    setTools([]);
    setScan([]);
    setApprovals([]);
    setFileChanges([]);
    setVerifications([]);
    setStatus("idle");
    try {
      await activateWorkspace(nextId);
      setNotice("");
    } catch {
      setNotice("Workspace 切换失败，请重试。");
    }
  }

  async function removeWorkspace(id: string) {
    if (!window.confirm("删除这个 Workspace 注册信息？本地代码不会被删除。")) return;
    stopRun("cancelled");
    try {
      await deleteWorkspace(id);
      const remaining = workspaces.filter((item) => item.id !== id);
      setWorkspaces(remaining);
      setWorkspaceId(remaining[0]?.id || "");
      setNotice("Workspace 已移除，本地文件未做任何修改。");
    } catch {
      setNotice("Workspace 删除失败，请稍后重试。");
    }
  }

  async function runScan() {
    if (!workspaceId) return;
    setNotice("正在扫描项目结构……");
    try {
      const result = await scanWorkspace(workspaceId);
      setScan(result as ScanSummary[]);
      setNotice(`扫描完成，识别到 ${result.length} 个仓库。`);
    } catch {
      setNotice("扫描失败，请检查目录是否仍在允许根目录内。");
    }
  }

  async function sendMessage(message = draft) {
    const trimmed = message.trim();
    if (!trimmed || !workspaceId || isRunLocked(status)) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const generation = ++generationRef.current;
    terminalRef.current = { generation, status: null };
    const assistantId = makeId();
    const sessionId = sessionIdsRef.current.get(workspaceId) ?? makeId();
    sessionIdsRef.current.set(workspaceId, sessionId);
    setMessages((items) => [
      ...items,
      { id: makeId(), role: "user", content: trimmed },
      { id: assistantId, role: "assistant", content: "" },
    ]);
    setDraft("");
    setTools([]);
    setApprovals([]);
    setFileChanges([]);
    setVerifications([]);
    setNotice("");
    setStatus("connecting");

    try {
      await streamChat(
        { message: trimmed, sessionId, workspaceId },
        (event) => handleAgentEvent(event, generation, assistantId),
        { signal: controller.signal },
      );
      if (generation === generationRef.current) {
        const terminal = terminalRef.current;
        if (terminal.generation === generation && terminal.status) {
          setStatus(terminal.status);
        } else {
          setStatus("failed");
          setNotice("连接在完成事件前中断，请重试本轮任务。");
        }
      }
    } catch (error) {
      if (generation !== generationRef.current) return;
      if (isAbort(error)) setStatus("cancelled");
      else {
        setStatus("failed");
        setNotice("小瑶暂时无法连接 Agent 引擎，请确认服务状态后重试。");
      }
    }
  }

  function handleAgentEvent(event: AgentEvent, generation: number, assistantId: string) {
    if (generation !== generationRef.current) return;
    const data = asRecord(event.data);
    const terminal = terminalRef.current;
    if (terminal.generation === generation && terminal.status) return;
    if (event.event === "message") {
      const token = typeof data.token === "string" ? data.token : "";
      setStatus("streaming");
      setMessages((items) => items.map((item) =>
        item.id === assistantId ? { ...item, content: item.content + token } : item));
    } else if (event.event === "tool") {
      setStatus("streaming");
      const name = typeof data.tool_name === "string" ? data.tool_name : "开发工具";
      const callId = typeof data.tool_call_id === "string" ? data.tool_call_id : undefined;
      const toolStatus = typeof data.status === "string" ? data.status : "start";
      setTools((items) => updateToolActivity(items, name, toolStatus, callId));
    } else if (event.event === "file_change") {
      const proposalId = stringValue(data.proposal_id) ?? stringValue(data.tool_call_id) ?? makeId();
      const change: FileChange = {
        id: proposalId,
        toolCallId: stringValue(data.tool_call_id),
        workspaceId: stringValue(data.workspace_id),
        virtualPath: stringValue(data.virtual_path) ?? "未知文件",
        operation: stringValue(data.operation),
        beforeSha256: stringValue(data.before_sha256),
        afterSha256: stringValue(data.after_sha256),
      };
      setFileChanges((items) => [...items.filter((item) => item.id !== proposalId), change]);
    } else if (event.event === "verification") {
      const toolCallId = stringValue(data.tool_call_id) ?? makeId();
      const result: VerificationResult = {
        id: toolCallId,
        toolCallId: stringValue(data.tool_call_id),
        profile: stringValue(data.profile) ?? "default",
        status: stringValue(data.status) ?? "error",
        exitCode: typeof data.exit_code === "number" ? data.exit_code : null,
        truncated: data.truncated === true,
      };
      setVerifications((items) => [...items.filter((item) => item.id !== toolCallId), result]);
    } else if (event.event === "approval_required") {
      const payload = asRecord(data.payload);
      const approvalId = typeof data.approval_id === "string" ? data.approval_id : makeId();
      const approval: Approval = {
        id: approvalId,
        action: typeof data.action === "string" ? data.action : "sensitive_action",
        message: typeof data.message === "string" ? data.message : "此操作需要你的确认",
        repository: stringValue(payload.repository),
        virtualPath: stringValue(payload.virtual_path),
        operation: stringValue(payload.operation),
        diff: stringValue(payload.diff),
        validationProfile: stringValue(payload.validation_profile),
        status: "pending",
      };
      setApprovals((items) => [...items.filter((item) => item.id !== approvalId), approval]);
    } else if (event.event === "error") {
      terminalRef.current = { generation, status: "failed" };
      setStatus("failed");
      setNotice(safeAgentError(data.code));
    } else if (event.event === "done") {
      const terminalStatus: TerminalStatus = data.status === "failed"
        ? "failed"
        : data.status === "awaiting_approval" ? "awaiting_approval" : "completed";
      terminalRef.current = { generation, status: terminalStatus };
      setStatus(terminalStatus);
    }
  }

  async function resumeApproval(approval: Approval, approved: boolean, reason?: string) {
    if (approval.status !== "pending" || approvalSubmissionsRef.current.has(approval.id)) return;
    const sessionId = sessionIdsRef.current.get(workspaceId);
    if (!sessionId) return;
    approvalSubmissionsRef.current.add(approval.id);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const generation = ++generationRef.current;
    terminalRef.current = { generation, status: null };
    const assistantId = [...messages].reverse().find((message) => message.role === "assistant")?.id ?? makeId();
    setApprovals((items) => items.map((item) => item.id === approval.id
      ? { ...item, status: "submitting" } : item));
    setNotice("");
    setStatus("resuming");

    try {
      await streamResume({
        sessionId,
        workspaceId,
        approvalId: approval.id,
        approved,
        reason,
      }, (event) => handleAgentEvent(event, generation, assistantId), { signal: controller.signal });
      if (generation !== generationRef.current) return;
      const terminal = terminalRef.current;
      if (terminal.generation !== generation || !terminal.status) {
        setStatus("failed");
        setNotice("连接在完成事件前中断，请重试本轮任务。");
        setApprovals((items) => items.map((item) => item.id === approval.id
          ? { ...item, status: "failed" } : item));
        return;
      }
      setApprovals((items) => items.map((item) => item.id === approval.id
        ? { ...item, status: terminal.status === "failed"
          ? "failed" : approved ? "approved" : "rejected" } : item));
    } catch (error) {
      if (generation !== generationRef.current) return;
      setStatus(isAbort(error) ? "cancelled" : "failed");
      if (!isAbort(error)) setNotice("审批提交失败，请检查服务状态后重试。");
      setApprovals((items) => items.map((item) => item.id === approval.id
        ? { ...item, status: "failed" } : item));
    } finally {
      approvalSubmissionsRef.current.delete(approval.id);
    }
  }

  function stopRun(nextStatus: RunStatus = "cancelled") {
    generationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus((current) => current === "connecting" || current === "streaming" || current === "resuming"
      ? nextStatus : current);
  }

  const busy = status === "connecting" || status === "streaming" || status === "resuming";
  const composerLocked = busy || status === "awaiting_approval";

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-workbench">跳到开发工作台</a>
      <header className="topbar">
        <button className="brand-mark" type="button" aria-label="展开或收起 Workspace"
          onClick={() => setWorkspaceOpen((open) => !open)}>
          <span aria-hidden="true">瑶</span>
        </button>
        <div className="brand-copy">
          <p className="eyebrow">LOCAL DEV AGENT · ALPHA</p>
          <h1>小瑶开发助手</h1>
        </div>
        <div className="runtime-pill" role="status">
          <span className={`status-dot status-${status}`} aria-hidden="true" />
          {statusLabel(status)}
        </div>
      </header>

      <div className={`workbench ${workspaceOpen ? "" : "sidebar-closed"}`}>
        <aside className="workspace-rail" aria-label="Workspace 列表"
          aria-hidden={!workspaceOpen} inert={!workspaceOpen}>
          <div className="rail-heading">
            <div><span className="section-index">01</span><h2>Workspace</h2></div>
            <button className="icon-button" type="button" onClick={() => setCreateOpen(true)}
              aria-label="添加 Workspace">＋</button>
          </div>
          {loadingWorkspaces ? <p className="muted">正在读取工作台…</p> : null}
          {!loadingWorkspaces && workspaces.length === 0 ? (
            <button className="empty-workspace" type="button" onClick={() => setCreateOpen(true)}>
              <span aria-hidden="true">⌁</span>
              <strong>打开第一个本地项目</strong>
              <small>登记 Java 服务可访问的文件夹</small>
            </button>
          ) : null}
          <nav className="workspace-list" aria-label="已登记的 Workspace">
            {workspaces.map((workspace) => (
              <button key={workspace.id} type="button"
                className={workspace.id === workspaceId ? "workspace-card active" : "workspace-card"}
                aria-current={workspace.id === workspaceId ? "true" : undefined}
                onClick={() => void chooseWorkspace(workspace.id)}>
                <span className="workspace-monogram" aria-hidden="true">
                  {workspace.name.slice(0, 1).toUpperCase()}
                </span>
                <span className="workspace-meta">
                  <strong>{workspace.name}</strong>
                  <small>{workspace.repositories?.length ?? 0} 个仓库 · {workspace.id}</small>
                </span>
                <span className="workspace-arrow" aria-hidden="true">↗</span>
              </button>
            ))}
          </nav>
          {selectedWorkspace ? (
            <div className="rail-actions">
              <button type="button" onClick={() => void runScan()}>重新扫描</button>
              <button type="button" className="danger-link"
                onClick={() => void removeWorkspace(selectedWorkspace.id)}>移除</button>
            </div>
          ) : null}
          <div className="local-note">
            <span aria-hidden="true">⌂</span>
            <p><strong>代码留在本机</strong><br /><small>浏览器只发送路径登记与开发指令。</small></p>
          </div>
        </aside>

        {compactLayout && workspaceOpen ? (
          <button className="mobile-scrim" type="button" aria-label="关闭 Workspace 列表"
            onClick={() => setWorkspaceOpen(false)} />
        ) : null}

        <main id="main-workbench" className="conversation" aria-busy={busy}
          inert={compactLayout && workspaceOpen}>
          <section className="context-strip" aria-label="当前项目上下文">
            <div>
              <span className="section-index">02</span>
              <p className="context-label">CURRENT BENCH</p>
              <strong>{selectedWorkspace?.name ?? "尚未选择 Workspace"}</strong>
            </div>
            <div className="context-actions">
              {scan.flatMap((item) => item.languages ?? []).slice(0, 4).map((language) =>
                <span className="tech-chip" key={language}>{language}</span>)}
              <button type="button" onClick={() => setWorkspaceOpen((open) => !open)}>
                {workspaceOpen ? "收起项目栏" : "展开项目栏"}
              </button>
            </div>
          </section>

          <section className="conversation-scroll" aria-label="开发对话记录">
            {messages.length === 0 ? (
              <div className="welcome-panel">
                <p className="welcome-kicker">READY WHEN YOU ARE</p>
                <h2>小瑶主人，<br />我来帮你开发了。</h2>
                <p>选择一个 Workspace，然后告诉我今天要完成什么。我会先读取项目，再把每一步工具动作摆在台面上。</p>
                <div className="prompt-grid">
                  {QUICK_PROMPTS.map((prompt, index) => (
                    <button type="button" key={prompt} disabled={!workspaceId}
                      onClick={() => void sendMessage(prompt)}>
                      <span>0{index + 1}</span>{prompt}<b aria-hidden="true">→</b>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="message-list">
                {messages.map((message) => (
                  <article key={message.id} className={`message ${message.role}`}>
                    <p className="message-role">{message.role === "user" ? "主人" : "小瑶"}</p>
                    <div>{message.content || (busy ? <span className="typing">正在理解项目<span>…</span></span> : "未返回内容")}</div>
                  </article>
                ))}
              </div>
            )}

            {tools.length > 0 || approvals.length > 0 || fileChanges.length > 0 || verifications.length > 0 ? (
              <aside className="activity-ledger" aria-label="工具活动">
                <div className="ledger-title"><span>施工记录</span>
                  <small>{tools.length + fileChanges.length + verifications.length} 项执行记录</small></div>
                {tools.map((tool) => (
                  <div className="tool-row" key={tool.id}>
                    <span className={`tool-check ${tool.status}`} aria-hidden="true" />
                    <code>{tool.name}</code><small>{toolStatusLabel(tool.status)}</small>
                  </div>
                ))}
                {approvals.map((approval) => (
                  <ApprovalCard key={approval.id} approval={approval} onDecision={resumeApproval} />
                ))}
                {fileChanges.map((change) => <FileChangeCard key={change.id} change={change} />)}
                {verifications.map((result) => <VerificationCard key={result.id} result={result} />)}
              </aside>
            ) : null}
          </section>

          <div className="live-region" aria-live="polite" role="status">
            {notice || (status === "completed" ? "小瑶已完成这轮开发分析。" : "")}
          </div>

          <form className="composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
            <label htmlFor="developer-request">告诉小瑶你想分析、修改或验证什么</label>
            <textarea id="developer-request" name="developer-request" value={draft}
              onChange={(event) => setDraft(event.target.value)} disabled={!workspaceId || composerLocked}
              placeholder={workspaceId ? "例如：先帮我梳理登录模块，再给出安全的修改计划…" : "先打开一个 Workspace"}
              rows={3}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void sendMessage();
                }
              }} />
            <div className="composer-footer">
              <span><kbd>Enter</kbd> 发送 · <kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span>
              {busy ? (
                <button className="stop-button" type="button" onClick={() => stopRun()}>停止</button>
              ) : (
                <button className="send-button" type="submit"
                  disabled={!workspaceId || composerLocked || !draft.trim()}>
                  开始协作 <span aria-hidden="true">↗</span>
                </button>
              )}
            </div>
          </form>
        </main>
      </div>

      {createOpen ? <WorkspaceDialog onClose={() => setCreateOpen(false)} onCreated={async (workspace) => {
        setCreateOpen(false);
        await refreshWorkspaces();
        await chooseWorkspace(workspace.id);
      }} /> : null}
    </div>
  );
}

function WorkspaceDialog({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (workspace: Workspace) => Promise<void>;
}) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [repositoryName, setRepositoryName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [hint, setHint] = useState("浏览器不会上传源码；Java 服务会按允许根目录读取该路径。");
  const [saving, setSaving] = useState(false);
  const dialogRef = useRef<HTMLElement>(null);
  const repositoryNameRef = useRef<HTMLInputElement>(null);
  const localPathRef = useRef<HTMLInputElement>(null);
  const onCloseRef = useRef(onClose);
  const previousFocusRef = useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  onCloseRef.current = onClose;

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? []);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previousFocusRef.current?.focus();
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (Boolean(repositoryName.trim()) !== Boolean(localPath.trim())) {
      setHint("仓库名称和绝对路径需要一起填写；也可以都留空，稍后再登记仓库。");
      (repositoryName.trim() ? localPathRef.current : repositoryNameRef.current)?.focus();
      return;
    }
    setSaving(true);
    try {
      let workspace = await createWorkspace({ id: id.trim(), name: name.trim() });
      if (repositoryName.trim() && localPath.trim()) {
        workspace = await addRepository(workspace.id, {
          name: repositoryName.trim(), localPath: localPath.trim(),
        });
        await scanWorkspace(workspace.id);
      }
      await activateWorkspace(workspace.id);
      await onCreated(workspace);
    } catch {
      setHint("登记失败。请检查 ID 是否重复，以及路径是否位于后端允许根目录内。");
    } finally {
      setSaving(false);
    }
  }

  async function pickDirectoryName() {
    const picker = (window as Window & {
      showDirectoryPicker?: () => Promise<{ name: string }>;
    }).showDirectoryPicker;
    if (!picker) {
      setHint("当前浏览器不能提供目录选择。请直接填写 Java 服务所在机器上的绝对路径。");
      return;
    }
    try {
      const handle = await picker();
      setRepositoryName((current) => current || handle.name);
      setHint(`已选择“${handle.name}”。受浏览器安全限制，请再填写 Java 服务可访问的绝对路径。`);
    } catch (error) {
      if (!isAbort(error)) setHint("目录选择未完成，请重试或手动填写路径。");
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <section ref={dialogRef} className="workspace-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-dialog-title">
        <button className="dialog-close" type="button" onClick={onClose} aria-label="关闭 Workspace 对话框">×</button>
        <span className="section-index">NEW BENCH</span>
        <h2 id="workspace-dialog-title">打开本地代码</h2>
        <p>创建一个只属于当前机器的开发工作台。源码不会上传到小瑶页面。</p>
        <form onSubmit={(event) => void submit(event)}>
          <div className="field-row">
            <label>显示名称<input name="workspace-name" value={name} required autoFocus autoComplete="off"
              onChange={(event) => { setName(event.target.value); if (!id) setId(slugify(event.target.value)); }}
              placeholder="订单服务" /></label>
            <label>Workspace ID<input name="workspace-id" value={id} required pattern="[A-Za-z0-9._-]+" autoComplete="off"
              onChange={(event) => setId(event.target.value)} placeholder="order-service" /></label>
          </div>
          <label>仓库名称<input ref={repositoryNameRef} name="repository-name" value={repositoryName}
            autoComplete="off" aria-describedby="workspace-form-hint"
            onChange={(event) => setRepositoryName(event.target.value)} placeholder="backend" /></label>
          <label>Java 服务可访问的绝对路径
            <div className="path-field"><input ref={localPathRef} name="local-path" value={localPath}
              autoComplete="off" aria-describedby="workspace-form-hint"
              onChange={(event) => setLocalPath(event.target.value)}
              placeholder="C:\\Users\\you\\code\\project 或 /workspace/project" />
              <button type="button" onClick={() => void pickDirectoryName()}>选择目录</button></div>
          </label>
          <p id="workspace-form-hint" className="form-hint" role="status">{hint}</p>
          <div className="dialog-actions">
            <button type="button" onClick={onClose}>取消</button>
            <button className="primary-action" type="submit" disabled={saving}>{saving ? "正在打开…" : "创建工作台"}</button>
          </div>
        </form>
      </section>
    </div>
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
}

function updateToolActivity(
  items: ToolActivity[], name: string, status: string, callId?: string,
): ToolActivity[] {
  const activityId = callId ?? makeId();
  if (status === "start") return [...items, { id: activityId, name, status }];
  if (callId) {
    const match = items.find((item) => item.id === callId);
    if (match) return items.map((item) => item.id === callId ? { ...item, name, status } : item);
    return [...items, { id: callId, name, status }];
  }
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.name === name && item.status === "start") {
      return items.map((candidate, candidateIndex) =>
        candidateIndex === index ? { ...candidate, status } : candidate);
    }
  }
  return [...items, { id: makeId(), name, status }];
}

function toolStatusLabel(status: string) {
  if (status === "end") return "已完成";
  if (status === "error") return "执行失败";
  return "执行中";
}

function safeAgentError(code: unknown) {
  if (code === "upstream_unavailable") return "Agent 引擎暂时不可用，请稍后重试。";
  if (code === "workspace_not_found") return "当前 Workspace 不存在，请重新选择。";
  return "Agent 执行失败，请重试。";
}

function stringValue(value: unknown) {
  return typeof value === "string" && value ? value : undefined;
}

function isRunLocked(status: RunStatus) {
  return status === "connecting" || status === "streaming"
    || status === "awaiting_approval" || status === "resuming";
}

function matchesMedia(query: string) {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}

function useMediaQuery(query: string) {
  const [matches, setMatches] = useState(() => matchesMedia(query));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);
  return matches;
}

function isAbort(error: unknown) {
  return (error instanceof DOMException && error.name === "AbortError")
    || (typeof error === "object" && error !== null && "name" in error
      && (error as { name?: unknown }).name === "AbortError");
}

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

function slugify(value: string) {
  return value.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-|-$/g, "");
}

function statusLabel(status: RunStatus) {
  return ({
    idle: "等待指令", connecting: "连接引擎", streaming: "协作中",
    awaiting_approval: "等待主人确认", resuming: "正在恢复任务",
    completed: "本轮完成", failed: "需要处理", cancelled: "已停止",
  })[status];
}
