# 小瑶开发助手发布说明

## 版本与范围

当前发布版本为 `0.1.0`。Python Engine、React Web、根级前端门禁和 Java 服务使用同一版本号。本版本面向可信机器上的本地单用户开发，不是公网多租户服务。

已交付的主链路包括：本地 Workspace 登记与扫描、受限文件检索、对话与 SSE、轻量 RAG 引用、受控代码提案、逐项 Diff 审批、原子写入、固定测试验证，以及审批断线和进程重启恢复。默认容器模式保持只读；只有显式加载 `docker-compose.write.yml` 才提供多语言可写验证环境。

## 可复现验收

发布前使用 Python 3.12、uv 0.13.5、Node.js 20.19.x 和 Java 17 安装冻结依赖，然后从仓库根目录执行：

```bash
npm ci --prefix web
uv sync --project engine --frozen --extra dev
uv run --project engine --frozen --extra dev python scripts/verify.py
```

该统一门禁会检查 Python 锁文件与编译、运行 Engine 测试，并通过根级 `npm test` 执行 Web 测试、生产构建、流隔离与架构契约，最后运行 Java 测试。

提供必要的非秘密占位环境变量后，还应检查 Compose 合并结果：

```bash
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.write.yml config --quiet
```

最终验收必须确认普通 Compose 中 `WORKSPACE_WRITE_ENABLED=false`、Workspace bind 为只读、数据存储不发布宿主端口，并确认可写 override 只改变 Engine 构建目标、写入开关和 Workspace bind。

GitHub Actions 还会真实构建默认与可写镜像，并以 `docker compose up -d --no-build --wait` 启动默认全栈，访问 Web 首页和 Java 健康端点后执行带 volume 与 orphan 清理的回收步骤。

## 运行可观测性

Engine 健康端点为 `/health`，Java 健康端点为 `/actuator/health`，Compose 会等待依赖健康后再启动 Web。Engine 为每个 HTTP 请求生成或保留安全的 `X-Request-ID`，在响应头和结构化日志上下文中返回该标识，便于关联故障。

## 已知边界

- 当前没有最终用户认证，不应直接暴露到公网。
- SQLite checkpoint、审批结果和 RAG 存储面向单 Engine 实例，不支持多实例共享锁。
- RAG 当前使用本地 FTS5 关键词检索，不等同于向量语义检索。
- 实际对话依赖用户配置的 OpenAI 兼容模型服务，发布验收不会携带或上传 API Key。

## 回滚

升级前应备份 MySQL volume 与 `engine-metadata` volume。应用发布回滚时，停止当前 Compose，切换到上一个已验收的标签或提交并重新构建；不要删除 Workspace 源代码或元数据卷。已经通过人工审批写入用户 Workspace 的代码属于用户变更，不应由部署回滚自动覆盖，应通过项目自身版本控制或新的受控变更恢复。

发布产物不得包含 `.env`、数据库、用户代码、内部计划、Agent 交接材料或任何真实凭据。
