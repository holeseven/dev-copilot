// @author lxy
import { beforeEach, describe, expect, it } from "vitest";

import {
  loadWorkspaceSession,
  removeWorkspaceSession,
  saveWorkspaceSession,
} from "./workspaceSessions";

describe("workspace session persistence", () => {
  beforeEach(() => localStorage.clear());

  it("stores only versioned workspace-to-session ids", () => {
    saveWorkspaceSession("workspace-1", "session-1");

    expect(loadWorkspaceSession("workspace-1")).toBe("session-1");
    expect(JSON.parse(localStorage.getItem("xiaoyao.workspace-sessions.v1") ?? "{}")).toEqual({
      version: 1,
      sessions: { "workspace-1": "session-1" },
    });
  });

  it("preserves other workspace sessions", () => {
    saveWorkspaceSession("workspace-1", "session-1");
    saveWorkspaceSession("workspace-2", "session-2");

    expect(loadWorkspaceSession("workspace-1")).toBe("session-1");
    expect(loadWorkspaceSession("workspace-2")).toBe("session-2");
  });

  it("removes only the deleted workspace session", () => {
    saveWorkspaceSession("workspace-1", "session-1");
    saveWorkspaceSession("workspace-2", "session-2");

    removeWorkspaceSession("workspace-1");

    expect(loadWorkspaceSession("workspace-1")).toBeUndefined();
    expect(loadWorkspaceSession("workspace-2")).toBe("session-2");
  });

  it("ignores corrupt, unversioned, and non-string values", () => {
    localStorage.setItem("xiaoyao.workspace-sessions.v1", "not-json");
    expect(loadWorkspaceSession("workspace-1")).toBeUndefined();
    localStorage.setItem("xiaoyao.workspace-sessions.v1", JSON.stringify({
      version: 2, sessions: { "workspace-1": "session-1" },
    }));
    expect(loadWorkspaceSession("workspace-1")).toBeUndefined();
    localStorage.setItem("xiaoyao.workspace-sessions.v1", JSON.stringify({
      version: 1, sessions: { "workspace-1": { approval: "must-not-be-stored" } },
    }));
    expect(loadWorkspaceSession("workspace-1")).toBeUndefined();
  });
});
