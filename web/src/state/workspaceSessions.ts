// @author lxy
const STORAGE_KEY = "xiaoyao.workspace-sessions.v1";

interface StoredSessions {
  version: 1;
  sessions: Record<string, string>;
}

export function loadWorkspaceSession(workspaceId: string): string | undefined {
  const stored = readSessions();
  const sessionId = stored.sessions[workspaceId];
  return typeof sessionId === "string" && sessionId ? sessionId : undefined;
}

export function saveWorkspaceSession(workspaceId: string, sessionId: string): void {
  if (!workspaceId || !sessionId) return;
  const stored = readSessions();
  const next: StoredSessions = {
    version: 1,
    sessions: { ...stored.sessions, [workspaceId]: sessionId },
  };
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage may be disabled. The active tab can continue with its in-memory session.
  }
}

export function removeWorkspaceSession(workspaceId: string): void {
  if (!workspaceId) return;
  const stored = readSessions();
  if (!(workspaceId in stored.sessions)) return;
  const sessions = { ...stored.sessions };
  delete sessions[workspaceId];
  try {
    if (Object.keys(sessions).length === 0) {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, sessions }));
    }
  } catch {
    // Storage may be disabled. In-memory state is cleared independently.
  }
}

function readSessions(): StoredSessions {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptySessions();
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed) || parsed.version !== 1 || !isRecord(parsed.sessions)) {
      return emptySessions();
    }
    const sessions = Object.fromEntries(Object.entries(parsed.sessions)
      .filter((entry): entry is [string, string] => typeof entry[1] === "string" && Boolean(entry[1])));
    return { version: 1, sessions };
  } catch {
    return emptySessions();
  }
}

function emptySessions(): StoredSessions {
  return { version: 1, sessions: {} };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
