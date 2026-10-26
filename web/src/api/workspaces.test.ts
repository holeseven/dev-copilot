// @author lxy
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  activateWorkspace,
  addRepository,
  createWorkspace,
  deleteWorkspace,
  getWorkspace,
  listWorkspaces,
  scanWorkspace,
} from "./workspaces";

describe("workspace API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("uses only the versioned Java workspace routes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ id: "alpha", name: "Alpha" }, 201))
      .mockResolvedValueOnce(jsonResponse({ id: "alpha", name: "Alpha" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ id: "alpha" }, 201))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ id: "alpha", active: true }));
    vi.stubGlobal("fetch", fetchMock);

    await listWorkspaces();
    await createWorkspace({ id: "alpha", name: "Alpha" });
    await getWorkspace("team/blue space");
    await deleteWorkspace("team/blue space");
    await addRepository("team/blue space", {
      name: "service",
      localPath: "C:/code/service",
    });
    await scanWorkspace("team/blue space");
    await activateWorkspace("team/blue space");

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/workspaces",
      "/api/v1/workspaces",
      "/api/v1/workspaces/team%2Fblue%20space",
      "/api/v1/workspaces/team%2Fblue%20space",
      "/api/v1/workspaces/team%2Fblue%20space/repositories",
      "/api/v1/workspaces/team%2Fblue%20space/scan",
      "/api/v1/workspaces/team%2Fblue%20space/activate",
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => init?.method ?? "GET")).toEqual([
      "GET", "POST", "GET", "DELETE", "POST", "POST", "POST",
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      id: "alpha",
      name: "Alpha",
    });
    expect(JSON.parse(fetchMock.mock.calls[4][1].body)).toEqual({
      name: "service",
      local_path: "C:/code/service",
    });
  });

  it("passes AbortSignal through to fetch", async () => {
    const signal = new AbortController().signal;
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await listWorkspaces({ signal });

    expect(fetchMock.mock.calls[0][1].signal).toBe(signal);
  });
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
