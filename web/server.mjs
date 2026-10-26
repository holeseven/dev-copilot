// @author lxy
import http from "node:http";
import { stat } from "node:fs/promises";
import { createReadStream } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CONTENT_TYPES = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".svg", "image/svg+xml"],
]);
const HOP_BY_HOP_HEADERS = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
  "te", "trailer", "transfer-encoding", "upgrade",
]);

export function createWebServer({
  distDirectory = path.join(path.dirname(fileURLToPath(import.meta.url)), "dist"),
  javaBaseUrl = process.env.JAVA_BASE_URL || "http://java-service:8081",
} = {}) {
  const distRoot = path.resolve(distDirectory);
  const javaBase = new URL(javaBaseUrl);
  if (!new Set(["http:", "https:"]).has(javaBase.protocol)) {
    throw new Error("JAVA_BASE_URL must use http or https");
  }

  return http.createServer((request, response) => {
    const requestUrl = new URL(request.url || "/", "http://localhost");
    if (requestUrl.pathname.startsWith("/api/")) {
      if (!requestUrl.pathname.startsWith("/api/v1/")) {
        sendJson(response, 404, { code: "not_found", message: "接口不存在" });
        return;
      }
      proxyToJava(request, response, requestUrl, javaBase);
      return;
    }
    void serveStatic(request, response, requestUrl, distRoot);
  });
}

async function serveStatic(request, response, requestUrl, distRoot) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    sendJson(response, 405, { code: "method_not_allowed", message: "请求方法不支持" });
    return;
  }

  let decodedPath;
  try {
    decodedPath = decodeURIComponent(requestUrl.pathname);
  } catch {
    sendJson(response, 400, { code: "bad_request", message: "请求路径无效" });
    return;
  }
  const relativePath = decodedPath === "/" ? "index.html" : decodedPath.replace(/^\/+/, "");
  const candidate = path.resolve(distRoot, relativePath);
  if (candidate !== distRoot && !candidate.startsWith(`${distRoot}${path.sep}`)) {
    sendJson(response, 400, { code: "bad_request", message: "请求路径无效" });
    return;
  }

  const filePath = await regularFile(candidate) ? candidate : path.join(distRoot, "index.html");
  if (!await regularFile(filePath)) {
    sendJson(response, 503, { code: "web_not_built", message: "前端资源尚未构建" });
    return;
  }
  response.writeHead(200, securityHeaders({
    "Content-Type": CONTENT_TYPES.get(path.extname(filePath)) || "application/octet-stream",
    "Cache-Control": filePath.endsWith("index.html") ? "no-cache" : "public, max-age=31536000, immutable",
  }));
  if (request.method === "HEAD") response.end();
  else createReadStream(filePath).pipe(response);
}

function proxyToJava(request, response, requestUrl, javaBase) {
  const upstreamUrl = new URL(`${requestUrl.pathname}${requestUrl.search}`, javaBase);
  const transport = upstreamUrl.protocol === "https:" ? import("node:https") : Promise.resolve(http);
  void transport.then((client) => {
    let activeUpstreamResponse;
    const headers = Object.fromEntries(Object.entries(request.headers)
      .filter(([name]) => !HOP_BY_HOP_HEADERS.has(name.toLowerCase())));
    headers.host = upstreamUrl.host;
    const upstream = client.request(upstreamUrl, {
      method: request.method,
      headers,
    }, (upstreamResponse) => {
      activeUpstreamResponse = upstreamResponse;
      const responseHeaders = Object.fromEntries(Object.entries(upstreamResponse.headers)
        .filter(([name]) => !HOP_BY_HOP_HEADERS.has(name.toLowerCase())));
      response.writeHead(upstreamResponse.statusCode || 502, securityHeaders(responseHeaders));
      upstreamResponse.pipe(response);
    });
    upstream.on("error", () => {
      if (!response.headersSent) {
        sendJson(response, 502, { code: "java_unavailable", message: "Java 服务暂时不可用" });
      } else {
        response.destroy();
      }
    });
    request.on("aborted", () => upstream.destroy());
    response.on("close", () => {
      if (!response.writableEnded) {
        activeUpstreamResponse?.destroy();
        upstream.destroy();
      }
    });
    request.pipe(upstream);
  }).catch(() => {
    sendJson(response, 502, { code: "java_unavailable", message: "Java 服务暂时不可用" });
  });
}

async function regularFile(filePath) {
  try {
    return (await stat(filePath)).isFile();
  } catch {
    return false;
  }
}

function securityHeaders(headers = {}) {
  return {
    ...headers,
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}

function sendJson(response, status, payload) {
  response.writeHead(status, securityHeaders({ "Content-Type": "application/json; charset=utf-8" }));
  response.end(JSON.stringify(payload));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const port = Number.parseInt(process.env.PORT || "8080", 10);
  createWebServer().listen(port, "0.0.0.0", () => {
    console.log(`xiaoyao web listening on ${port}`);
  });
}
