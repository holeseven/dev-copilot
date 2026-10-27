<!-- 作者：lxy -->

# 小瑶开发助手（Dev Copilot）

> 面向本地代码库的开发助手 Alpha：用 Workspace 管理多个仓库，通过 LangGraph 分析代码，并在主人确认 Diff 后受控修改和验证项目。

## 当前状态

项目目前是**本地单用户 Alpha**，不是已经完成的生产级 Agent 平台。当前已经过测试覆盖的主链路包括：

- 创建、查询、激活和删除 Workspace，并持久化本地注册元数据；
- 在一个 Workspace 中注册多个仓库，扫描语言、构建系统、服务、中间件、显式依赖和 Git 信息；
- 按 Workspace 隔离 `list_dir`、`find_files`、`grep_code`、`read_file`、`code_search` 五个只读工具；
- 通过 `preview_edit_file` / `preview_create_file` 生成单文件 UTF-8 变更提案，在执行 `apply_workspace_change` 前展示服务端生成的 Diff 并等待逐项批准；批准后原子写入，并按固定白名单运行 `pytest`、`mvn test` 或 `npm test`；
- LangGraph 模型/工具循环和 SSE token、工具状态事件；
- FastAPI 提供的单文件薄工作台，可在 Python 本地开发模式下切换 Workspace、注册仓库、扫描和发起只读问答；
- Java 提供版本化的统一聊天 SSE 与 Workspace API，并在容器模式中作为唯一公开业务入口；
- Java 到 Python 的 SSE 代理保留消息、工具、审批、文件变更和验证事件，支持 `/api/v1/agent/chat/resume` 恢复审批中的任务，区分连接前 HTTP 错误与流中失败，并向上游传播取消；
- LangGraph checkpoint 与受控修改提案持久化到本地 SQLite；Engine 重启后，工作台会按 Workspace 恢复待审批 Diff，并继续使用原会话批准或拒绝；
- Workspace RAG 使用本地 SQLite FTS5 按 Workspace 与代际隔离索引 Java、Python、前端源码及常用文档；支持显式后台重建、未变文件复用、增删更新、重启持久化和带行号来源引用；
- React 产品工作台“**小瑶开发助手**”：管理本地 Workspace、扫描项目、构建知识索引、流式对话、查看来源与工具施工记录，在内联 Diff 卡片中批准或拒绝单次修改，并展示写入和自动测试结果；页面只调用 Java `/api/v1`。

下列能力仍在开发中，请不要把现有占位模块视为可用功能：

- 面向多 Engine 实例的共享 checkpoint、分布式审批锁，以及长期审计记录；
- 向量语义检索、外部知识源同步和多实例共享 RAG 索引；
- 面向多人或公网部署的认证、授权和租户隔离。

因此，本版本只适合可信机器上的本地开发与评估。不要直接暴露到公网。

## 当前架构

```text
React 工作台 / API 客户端
       │ /api/v1 REST / SSE
       ▼
Java Spring Boot ───────────── MySQL / Redis
       │ 内部 REST / SSE
       ▼
Python FastAPI + LangGraph ── Workspace Registry / Scanner / RAG
       │                    └─ Workspace 隔离的读取、检索、变更提案、审批写入与测试运行时
       ├─ SQLite（checkpoint、待审批提案与本地知识索引）
       └─ Redis（动态配置与现有基础设施）
```

## 安全边界

- Python 本地开发默认监听 `127.0.0.1`；Compose 不发布 Engine 端口，只把 Java 映射到宿主机 loopback。
- Redis 与 MySQL 只加入 Compose 内部网络，不发布宿主端口。
- `DEV_COPILOT_ALLOWED_ROOT` 以只读方式挂载为容器内 `/workspace`；注册表等可写元数据保存在独立 `engine-metadata` volume。
- 仓库注册路径可以是绝对宿主机/容器路径，但必须位于明确的允许根目录内。注册后，只读工具仅接受仓库内的虚拟相对路径，并拒绝绝对路径、盘符/UNC 路径、`..` 越界、损坏链接，以及通过 symlink/junction（Windows reparse point）逃逸；无法检查或解析时 fail closed。
- 本地进程模式下，代码修改采用“预览提案 → 展示 Diff → 单次人工批准 → 原子写入 → 固定测试配置验证”的闭环；提案绑定 Workspace、目标文件内容哈希和审批 ID，目标在批准前发生变化时会拒绝写入。测试命令及参数由服务端白名单确定，不接受模型提供任意 shell 命令。
- checkpoint 与服务端变更提案写入 `WORKSPACE_METADATA_DIR` 下的 SQLite 数据库；单 Engine 进程重启后可以恢复待审批任务。SQLite 方案面向本地单实例，尚不支持多个 Engine 实例共享审批锁，也不等同于长期审计系统。
- RAG 只索引允许根目录内已登记仓库的受支持文本文件，排除隐藏目录、依赖/构建产物、常见密钥文件、二进制和超限文件。索引内容保留在本机 SQLite，默认不调用外部 embedding 服务；当前 FTS5 是关键词检索，不等同于向量语义检索。
- 当前没有用户认证。允许根目录应尽量小，不要挂载整个用户目录、磁盘根目录或敏感凭据目录。

## Docker Compose 快速启动

安全 Compose 是**全容器模式**。先复制环境变量模板，并至少填写下面四项：

```bash
git clone https://github.com/holeseven/dev-copilot.git
cd dev-copilot
cp .env.example .env
```

```dotenv
DEV_COPILOT_MYSQL_ROOT_PASSWORD=请生成独立强密码
DEV_COPILOT_DB_PASSWORD=请生成另一独立强密码
DEV_COPILOT_ALLOWED_ROOT=/宿主机/允许读取的代码目录
LLM_API_KEY=你的OpenAI兼容模型Key
```

Windows Docker Desktop 可把允许根目录写成例如 `C:/Users/you/code`。确认 Docker Desktop 已授权共享该目录，然后启动：

```bash
docker compose up -d --build
```

启动完成后打开“小瑶开发助手”：<http://127.0.0.1:3000>。前端容器只代理版本化的 `/api/v1` 到 Java；Java API 仍可在 <http://127.0.0.1:8081/api/v1> 直接访问。两个入口都只绑定宿主机 loopback。Python Engine 不发布宿主端口，只接受容器间调用，同时保留访问外部模型 API 的网络出口。

安全 Compose 当前把 `/workspace` 挂载为只读，因此容器模式可完成项目登记、扫描、代码分析和对话，但不能实际写入受控变更。需要体验修改闭环时，请使用下面的本地进程开发模式，并只把确实允许修改的代码目录加入 `WORKSPACE_ALLOWED_ROOTS`。

在 Docker 模式注册仓库时必须使用**容器路径**。若宿主目录 `${DEV_COPILOT_ALLOWED_ROOT}` 下有 `order-service`，请求应为：

```bash
curl -X POST http://127.0.0.1:8081/api/v1/workspaces \
  -H "Content-Type: application/json" \
  -d '{"id":"local-dev","name":"Local Dev"}'

curl -X POST http://127.0.0.1:8081/api/v1/workspaces/local-dev/repositories \
  -H "Content-Type: application/json" \
  -d '{"name":"order-service","local_path":"/workspace/order-service"}'

curl -X POST http://127.0.0.1:8081/api/v1/workspaces/local-dev/scan
curl -X POST http://127.0.0.1:8081/api/v1/workspaces/local-dev/activate
curl -X POST http://127.0.0.1:8081/api/v1/workspaces/local-dev/rag/index/rebuild

curl -N -X POST http://127.0.0.1:8081/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"message":"分析项目入口","session_id":"demo-1","workspace_id":"local-dev"}'
```

Compose 没有发布 Redis/MySQL 端口，所以不要使用 `docker compose up redis mysql` 后再让宿主机进程连接它们。当前本地进程开发需自行提供 `localhost` Redis/MySQL；项目后续会提供单独的开发 override。

## Python 本地开发

本地开发统一使用 Python 3.12 和 [uv](https://docs.astral.sh/uv/)，并需要宿主机可访问的 Redis。随后在仓库根目录运行：

```bash
uv python install 3.12
uv sync --project engine --frozen --extra dev

# Linux/macOS 示例；PowerShell 请使用 $env:变量名="值"
export LLM_API_KEY="你的Key"
export REDIS_URL="redis://127.0.0.1:6379/0"
export WORKSPACE_ALLOWED_ROOTS='["/absolute/path/to/code"]'
export WORKSPACE_METADATA_DIR="$HOME/.dev-copilot"

uv run --project engine --frozen uvicorn engine.main:app --reload --host 127.0.0.1 --port 8000
```

本地模式注册仓库时使用真实宿主路径，而不是 `/workspace`：

```bash
curl -X POST http://127.0.0.1:8000/workspaces \
  -H "Content-Type: application/json" \
  -d '{"id":"local-dev","name":"Local Dev"}'

curl -X POST http://127.0.0.1:8000/workspaces/local-dev/repositories \
  -H "Content-Type: application/json" \
  -d '{"name":"order-service","local_path":"/absolute/path/to/code/order-service"}'
```

Windows 请求体可使用 `C:\\Users\\you\\code\\order-service`。所有仓库仍必须落在 `WORKSPACE_ALLOWED_ROOTS` 之内。

登记仓库后，可在“小瑶开发助手”侧栏点击“构建知识索引”。重建请求返回 `202 Accepted`，页面会轮询构建状态；索引就绪后，Agent 可以调用只读的 `search_workspace`，来源会以 `仓库/相对路径:行号` 显示在对应回答下方。修改仓库登记会产生新的 Workspace 代际，旧索引不会跨代继续提供结果，需要重新构建。

## React 工作台本地开发

先启动 Python Engine 和 Java 服务，确认 Java 可通过 `http://127.0.0.1:8081/api/v1` 访问；然后在仓库根目录运行：

```bash
npm ci --prefix web
npm --prefix web run dev
```

打开终端输出的本地地址（默认 <http://127.0.0.1:5173>）。Vite 只把 `/api` 转发到 Java `8081`，浏览器不会直连 Python Engine。

点击“添加 Workspace”可登记本地项目。浏览器目录选择器受安全模型限制，不能把绝对路径交给后端，因此页面只用它辅助填写仓库名称；仍需填写 **Java/Python 服务实际可访问的绝对路径**。Docker 模式下应填写 `/workspace/...`，本地进程模式下填写宿主机真实路径。源码不会被浏览器上传。

## 一键验证与 CI

本地一键验证要求预先安装 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20.19.x、Java 17；Java 测试使用仓库自带的 Maven Wrapper，首次运行需要联网下载 Maven。先安装 Web 依赖，再从仓库根目录运行门禁：

```bash
npm ci --prefix web
uv run --project engine --frozen --extra dev python scripts/verify.py
```

门禁会按顺序检查 `uv.lock`，编译并测试 Python，测试并构建 React 工作台，最后运行 Java 单元测试；任一步失败都会立即停止。GitHub Actions 会在 push 和 pull request 上使用相同门禁，并以冻结的 Python 依赖安装保证锁文件不被 CI 隐式更新。

## 环境变量

| 变量 | 用途 | 默认值/要求 |
|---|---|---|
| `DEV_COPILOT_MYSQL_ROOT_PASSWORD` | Compose 初始化 MySQL root 密码 | Compose 必填 |
| `DEV_COPILOT_DB_USERNAME` | Java 应用数据库账户 | `dev_copilot` |
| `DEV_COPILOT_DB_PASSWORD` | Java 应用数据库密码 | Compose 必填 |
| `DEV_COPILOT_ENGINE_BASE_URL` | Java 访问 Python Engine 的内部地址 | 本地 `http://127.0.0.1:8000`；Compose 固定为 `http://engine:8000` |
| `DEV_COPILOT_ALLOWED_ROOT` | Compose 允许读取的宿主代码根目录 | Compose 必填、只读挂载 |
| `LLM_API_KEY` | OpenAI 兼容模型 Key | 实际问答必填 |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 默认/L1 模型 | `deepseek-chat` |
| `LLM_MODEL_L2` | L2 模型；空值时复用默认模型 | 空 |
| `REDIS_URL` | Python Redis 地址 | `redis://localhost:6379/0` |
| `WORKSPACE_ALLOWED_ROOTS` | Python 本地模式允许根目录；JSON 数组或系统路径分隔列表 | 回退到 `WORKSPACE_DIR` |
| `WORKSPACE_METADATA_DIR` | Workspace 注册元数据目录 | `~/.dev-copilot` |
| `CHECKPOINT_DB_PATH` | LangGraph checkpoint SQLite 路径 | `${WORKSPACE_METADATA_DIR}/checkpoints.sqlite3` |
| `PROPOSAL_DB_PATH` | 受控修改提案 SQLite 路径 | `${WORKSPACE_METADATA_DIR}/proposals.sqlite3` |
| `RAG_DB_PATH` | Workspace 本地知识索引 SQLite 路径 | `${WORKSPACE_METADATA_DIR}/rag.sqlite3` |
| `APP_HOST` | Python 监听地址 | `127.0.0.1` |
| `APP_PORT` | Python 监听端口 | `8000` |

完整 Compose 可调项见 [.env.example](.env.example)。

## 项目结构

```text
dev-copilot/
├── docker-compose.yml        # 本地全容器编排与安全网络边界
├── .env.example              # 无凭据的环境变量模板
├── engine/                   # Python FastAPI / LangGraph Agent 引擎
│   ├── api/                  # chat、tasks、tools、workspaces、rag 路由
│   ├── core/workspace/       # Registry、PathGuard、Scanner、受控变更服务
│   ├── core/rag/             # 增量切块、SQLite FTS5 索引与来源引用
│   ├── core/mcp/             # Workspace 隔离的读取、变更提案和审批工具
│   ├── core/execution/       # 固定白名单测试配置与受限进程执行
│   ├── core/hitl/            # LangGraph 人工审批门控
│   ├── orchestrator/         # 当前接入主链路的 LangGraph 图和节点
│   ├── frontend/index.html   # 当前单文件薄工作台
│   └── tests/
├── java-service/             # Spring Boot 统一 API 与数据服务
└── web/                      # React 19 / TypeScript / Vite 产品工作台
```

`engine/core/rag` 已接入 Workspace 知识索引主链路；`engine/core/openspec`、部分 `engine/core/orchestrator` 与 `agents` 文件仍是实验或演进中的模块，尚未形成完整产品主链路。

## API 使用提示

- 公开 Workspace 接口位于 Java `/api/v1/workspaces`；创建、查询、删除、注册仓库、扫描和激活操作由 Java 转发到内部 Python Engine。
- `POST /api/v1/agent/chat` 必须提供 `message`、`session_id` 和 `workspace_id`，通过 SSE 返回消息、工具状态、审批、文件变更和验证事件。
- `POST /api/v1/agent/chat/pending` 使用 `session_id` 和 `workspace_id` 查询当前待审批项；React 只把这两个不透明 ID 保存在浏览器本地，不保存代码、Diff 或提示词。
- 收到 `approval_required` 后，使用相同的 `session_id`、`workspace_id` 和服务端签发的 `approval_id` 调用 `POST /api/v1/agent/chat/resume`，传入 `approved` 及可选的 `reason`。批准只对应当前一项 Diff；拒绝不会写入该变更。
- 恢复状态由 Python Engine 的 SQLite checkpoint 与服务端提案库共同提供；同一 metadata 目录下重启 Engine 可以继续原审批。多实例部署仍需要共享数据库与分布式锁，当前版本不支持。

## License

[MIT License](LICENSE)

作者：lxy
