// @author lxy
// @vitest-environment node
import { afterEach, describe, expect, it } from "vitest";
import http from "node:http";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { createWebServer } from "./server.mjs";

const servers = [];
const tempDirectories = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))));
  await Promise.all(tempDirectories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
});

describe("production web server", () => {
  it("serves the SPA with security headers and blocks path traversal", async () => {
    const dist = await makeDist();
    const server = await listen(createWebServer({ distDirectory: dist }));

    const page = await fetch(`${address(server)}/settings`);
    expect(page.status).toBe(200);
    expect(await page.text()).toContain("小瑶开发助手");
    expect(page.headers.get("content-security-policy")).toContain("connect-src 'self'");

    const traversal = await fetch(`${address(server)}/..%2Fsecret.txt`);
    expect(traversal.status).toBe(400);
  });

  it("streams only versioned API calls to Java and preserves the request body", async () => {
    let observed = null;
    const java = await listen(http.createServer((request, response) => {
      let body = "";
      request.setEncoding("utf8");
      request.on("data", (chunk) => { body += chunk; });
      request.on("end", () => {
        observed = { method: request.method, url: request.url, body };
        response.writeHead(200, { "Content-Type": "text/event-stream" });
        response.end("event: done\ndata: {\"status\":\"completed\"}\n\n");
      });
    }));
    const dist = await makeDist();
    const web = await listen(createWebServer({ distDirectory: dist, javaBaseUrl: address(java) }));

    const response = await fetch(`${address(web)}/api/v1/agent/chat?trace=1`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: '{"message":"hello"}',
    });
    expect(await response.text()).toContain("event: done");
    expect(observed).toEqual({
      method: "POST",
      url: "/api/v1/agent/chat?trace=1",
      body: '{"message":"hello"}',
    });

    const rejected = await fetch(`${address(web)}/api/chat`);
    expect(rejected.status).toBe(404);
  });

  it("propagates a downstream SSE cancellation to Java", async () => {
    let markJavaClosed;
    const javaClosed = new Promise((resolve) => { markJavaClosed = resolve; });
    const java = await listen(http.createServer((_request, response) => {
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      response.write(": connected\n\n");
      response.on("close", () => markJavaClosed());
    }));
    const dist = await makeDist();
    const web = await listen(createWebServer({ distDirectory: dist, javaBaseUrl: address(java) }));

    await new Promise((resolve, reject) => {
      const downstream = http.get(`${address(web)}/api/v1/agent/chat`, (response) => {
        response.once("data", () => {
          downstream.destroy();
          resolve();
        });
      });
      downstream.on("error", (error) => {
        if (error.code !== "ECONNRESET") reject(error);
      });
    });

    await Promise.race([
      javaClosed,
      new Promise((_, reject) => setTimeout(() => reject(new Error("Java stream was not cancelled")), 1_000)),
    ]);
  });
});

async function makeDist() {
  const directory = await mkdtemp(path.join(os.tmpdir(), "xiaoyao-web-"));
  tempDirectories.push(directory);
  await writeFile(path.join(directory, "index.html"), "<h1>小瑶开发助手</h1>", "utf8");
  return directory;
}

async function listen(server) {
  servers.push(server);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return server;
}

function address(server) {
  const value = server.address();
  if (!value || typeof value === "string") throw new Error("server address unavailable");
  return `http://127.0.0.1:${value.port}`;
}
