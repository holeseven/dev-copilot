<!-- 作者：lxy -->

# 小瑶开发助手（Dev Copilot）

> 面向本地代码库的开发助手 Alpha：用 Workspace 管理多个仓库，通过 LangGraph 调用只读代码工具，并以 SSE 流式返回结果。

## 当前状态

项目目前是**本地单用户 Alpha**，不是已经完成的生产级 Agent 平台。当前已经过测试覆盖的主链路包括：

- 创建、查询、激活和删除 Workspace，并持久化本地注册元数据；
- 在一个 Workspace 中注册多个仓库，扫描语言、构建系统、服务、中间件、显式依赖和 Git 信息；
- 按 Workspace 隔离 `list_dir`、`find_files`、`grep_code`、`read_file`、`code_search` 五个只读工具；
- LangGraph 模型/工具循环和 SSE token、工具状态事件；
- FastAPI 提供的单文件薄工作台，可切换 Workspace、注册仓库、扫描和发起只读问答；
- Java 服务提供会话、记忆和配置的数据读写接口，但它目前**不是浏览器统一入口**，前端仍直接连接 Python FastAPI。

下列能力仍在开发中，请不要把现有占位模块视为可用功能：

- 可恢复、可审计的 HITL 受控代码修改闭环；
- Workspace RAG 索引、增量更新与来源引用；
- React 产品级工作台以及由 Java 统一代理浏览器 API/SSE；
- 面向多人或公网部署的认证、授权和租户隔离。

因此，本版本只适合可信机器上的本地开发与评估。不要直接暴露到公网。

## 当前架构

```text
浏览器薄工作台
       │ REST / SSE
       ▼
Python FastAPI + LangGraph ── Workspace Registry / Scanner
       │                    └─ 每个 Workspace 独立的只读工具运行时
       └─ Redis（动态配置与现有基础设施）

Java Spring Boot ── MySQL / Redis
  └─ 独立的数据服务；尚未接管浏览器入口，也尚未成为 Python 主链路的可靠持久化层
```

## 安全边界

- Python 本地启动默认监听 `127.0.0.1`；Compose 也只把 Engine 和 Java 映射到宿主机 loopback。
- Redis 与 MySQL 只加入 Compose 内部网络，不发布宿主端口。
- `DEV_COPILOT_ALLOWED_ROOT` 以只读方式挂载为容器内 `/workspace`；注册表等可写元数据保存在独立 `engine-metadata` volume。
- 仓库注册路径可以是绝对宿主机/容器路径，但必须位于明确的允许根目录内。注册后，只读工具仅接受仓库内的虚拟相对路径，并拒绝绝对路径、盘符/UNC 路径、`..` 越界、损坏链接，以及通过 symlink/junction（Windows reparse point）逃逸；无法检查或解析时 fail closed。
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

访问地址：

- 小瑶薄工作台：<http://127.0.0.1:8000>
- Python API 文档：<http://127.0.0.1:8000/docs>
- Java 数据服务 API 前缀：<http://127.0.0.1:8081/api/v1/agent>

在 Docker 模式注册仓库时必须使用**容器路径**。若宿主目录 `${DEV_COPILOT_ALLOWED_ROOT}` 下有 `order-service`，请求应为：

```bash
curl -X POST http://127.0.0.1:8000/workspaces \
  -H "Content-Type: application/json" \
  -d '{"id":"local-dev","name":"Local Dev"}'

curl -X POST http://127.0.0.1:8000/workspaces/local-dev/repositories \
  -H "Content-Type: application/json" \
  -d '{"name":"order-service","local_path":"/workspace/order-service"}'

curl -X POST http://127.0.0.1:8000/workspaces/local-dev/scan
curl -X POST http://127.0.0.1:8000/workspaces/local-dev/activate
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
curl -X POST http://127.0.0.1:8000/workspaces/local-dev/repositories \
  -H "Content-Type: application/json" \
  -d '{"name":"order-service","local_path":"/absolute/path/to/code/order-service"}'
```

Windows 请求体可使用 `C:\\Users\\you\\code\\order-service`。所有仓库仍必须落在 `WORKSPACE_ALLOWED_ROOTS` 之内。

运行 Python 测试：

```bash
uv run --project engine --frozen --extra dev pytest engine/tests -q
```

## 环境变量

| 变量 | 用途 | 默认值/要求 |
|---|---|---|
| `DEV_COPILOT_MYSQL_ROOT_PASSWORD` | Compose 初始化 MySQL root 密码 | Compose 必填 |
| `DEV_COPILOT_DB_USERNAME` | Java 应用数据库账户 | `dev_copilot` |
| `DEV_COPILOT_DB_PASSWORD` | Java 应用数据库密码 | Compose 必填 |
| `DEV_COPILOT_ALLOWED_ROOT` | Compose 允许读取的宿主代码根目录 | Compose 必填、只读挂载 |
| `LLM_API_KEY` | OpenAI 兼容模型 Key | 实际问答必填 |
| `LLM_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 默认/L1 模型 | `deepseek-chat` |
| `LLM_MODEL_L2` | L2 模型；空值时复用默认模型 | 空 |
| `REDIS_URL` | Python Redis 地址 | `redis://localhost:6379/0` |
| `WORKSPACE_ALLOWED_ROOTS` | Python 本地模式允许根目录；JSON 数组或系统路径分隔列表 | 回退到 `WORKSPACE_DIR` |
| `WORKSPACE_METADATA_DIR` | Workspace 注册元数据目录 | `~/.dev-copilot` |
| `APP_HOST` | Python 监听地址 | `127.0.0.1` |
| `APP_PORT` | Python 监听端口 | `8000` |

完整 Compose 可调项见 [.env.example](.env.example)。

## 项目结构

```text
dev-copilot/
├── docker-compose.yml        # 本地全容器编排与安全网络边界
├── .env.example              # 无凭据的环境变量模板
├── engine/                   # Python FastAPI / LangGraph Agent 引擎
│   ├── api/                  # chat、tasks、tools、workspaces 路由
│   ├── core/workspace/       # Registry、PathGuard、Scanner
│   ├── core/mcp/             # Workspace 隔离的只读工具运行时
│   ├── orchestrator/         # 当前接入主链路的 LangGraph 图和节点
│   ├── frontend/index.html   # 当前单文件薄工作台
│   └── tests/
└── java-service/             # Spring Boot 会话/记忆/配置数据服务
```

`engine/core/hitl`、`engine/core/rag`、`engine/core/openspec`、`engine/core/orchestrator` 和部分 `agents` 文件是实验或演进中的模块，目前未形成完整产品主链路。

## API 使用提示

- `GET /tools?workspace_id=local-dev` 返回当前 Workspace 的只读工具 schema。
- `POST /chat` 需要 `message`、`session_id` 和 `workspace_id`，通过 SSE 返回消息与工具状态。
- 未显式传 `workspace_id` 时会使用 active Workspace；没有 active Workspace 会返回冲突错误。
- `POST /chat/resume` 已有接口骨架，但当前图没有持久化 checkpointer，不能视为可靠的跨请求 HITL 恢复能力。

## License

MIT

作者：lxy
