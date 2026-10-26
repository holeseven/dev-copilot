// @author lxy
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "../..");
const webRoot = path.join(root, "web");

function sourceFiles(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(fullPath);
    return /\.(?:ts|tsx|js|jsx|html|css)$/.test(entry.name) ? [fullPath] : [];
  });
}

const productionFiles = sourceFiles(path.join(webRoot, "src"))
  .filter((file) => !/\.test\.[cm]?[jt]sx?$/.test(file) && !file.includes(`${path.sep}test${path.sep}`));
const source = productionFiles
  .map((file) => fs.readFileSync(file, "utf8"))
  .join("\n");
assert.doesNotMatch(source, /https?:\/\//, "production browser code must use same-origin URLs");
assert.doesNotMatch(source, /(?:localhost|127\.0\.0\.1):8000|engine(?::8000)?/);
assert.match(source, /\/api\/v1\/agent\/chat/);
assert.match(source, /\/api\/v1\/workspaces/);
assert.match(source, /小瑶开发助手/);
assert.match(source, /小瑶主人/);
for (const file of productionFiles) {
  const content = fs.readFileSync(file, "utf8");
  if (content.includes("fetch(")) {
    assert.ok(file.includes(`${path.sep}api${path.sep}`), `fetch is restricted to API clients: ${file}`);
  }
  for (const match of content.matchAll(/["'`](\/[^"'`\s]*)["'`]/g)) {
    const url = match[1];
    assert.ok(url.startsWith("/api/v1/"), `browser URL is outside Java /api/v1 in ${file}: ${url}`);
  }
  assert.doesNotMatch(content, /fetch\(\s*["'`](?!\/api\/v1\/)/, `direct browser endpoint in ${file}`);
}

const packageJson = JSON.parse(fs.readFileSync(path.join(webRoot, "package.json"), "utf8"));
for (const [name, version] of Object.entries({
  ...packageJson.dependencies,
  ...packageJson.devDependencies,
})) {
  assert.match(version, /^\d+\.\d+\.\d+$/, `${name} must use an exact version`);
}

const dist = sourceFiles(path.join(webRoot, "dist"))
  .map((file) => fs.readFileSync(file, "utf8"))
  .join("\n");
assert.doesNotMatch(dist, /https?:\/\/(?:localhost|127\.0\.0\.1|engine)(?::\d+)?/);

console.log("web architecture contract passed");
