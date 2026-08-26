<!-- @author lxy -->
# Dev Copilot —— AI 研发助手 Agent

一个辅助开发的个人 Agent 引擎项目。核心走 **OpenSpec 四阶段流程**（`proposal → design → tasks → apply`），
在关键节点通过 **HITL（Human-in-the-Loop）断点** 让用户参与决策，确保 AI 的每一步产出可控、可审、可回退。

> 技术栈：FastAPI + LangGraph + Redis + Docker（Python 引擎） / Spring Boot + MyBatis-Plus（Java 数据服务）

---

## 一、项目定位

Dev Copilot 是一个「规范驱动」的研发助手：不追求一步到位地生成代码，而是把一次开发拆成四个可审阅的阶段。

| 阶段 | 名称 | 产出 | HITL 断点 |
|------|------|------|-----------|
| 1 | **Proposal（提案）** | 需求理解、目标与范围 | 用户确认「要做什么」 |
| 2 | **Design（设计）** | 技术方案、架构与取舍 | 用户确认「怎么做」 |
| 3 | **Tasks（拆解）** | 可执行任务清单 + 依赖顺序 | 用户确认「分几步做」 |
| 4 | **Apply（实施）** | 落地代码变更 | 高风险操作前人工复核 |

---

## 二、架构概览

```
                    ┌─────────────────────────────────────────┐
                    │            FastAPI 引擎 (engine)          │
                    │                                           │
   用户 ──/chat──▶  │  Dispatcher 路由 → LangGraph 编排 → SSE  │ ──▶ 流式输出
                    │        │            │           │        │
                    │        ▼            ▼           ▼        │
                    │   Memory 记忆   MCP 工具链   HITL 断点    │
                    └────────┬──────────────┬───────────────────┘
                             │              │
                        ┌────▼────┐   ┌─────▼──────┐
                        │  Redis  │   │ Java 数据   │
                        │ 记忆/锁 │   │ 服务(持久化)│
                        │ 队列/配置│   └────────────┘
                        └─────────┘
```

核心模块（后续任务逐步实现）：

- **Dispatcher 智能路由**：Flash 模型 Structured Output，按复杂度 L1/L2 分流
- **CAS 任务抢占**：Redis SETNX + TTL 原子抢占（拉模式）
- **Memory 记忆**：Redis ZSET 存长期记忆 + 滑动窗口三层截断
- **MCP 工具链**：初始化 / 运行时 / 自愈 三层容错
- **多 Agent 协作**：LLM-as-Supervisor + write_todos 任务规划
- **RAG 知识库**：向量语义检索（区别于代码 agentic search）
- **HITL 人工复核**：LangGraph interrupt 高风险审核门控
- **Tracing 埋点**：分步骤耗时 + Token 消耗统计

---

## 三、目录结构

```
agent/
├── docker-compose.yml        # Redis + Python 引擎 +（Java 占位）一键启动
├── .env.example              # 配置项示例
├── engine/                   # Python Agent 引擎（核心）
│   ├── main.py               # FastAPI 入口（lifespan 初始化）
│   ├── config/settings.py    # Pydantic Settings 配置
│   ├── infra/                # 基础设施：redis / llm / dynamic_config
│   ├── core/                 # 核心模块（dispatcher/memory/mcp/... 后续填充）
│   ├── orchestrator/         # LangGraph 编排
│   ├── agents/               # 业务 Agent
│   ├── api/                  # FastAPI 路由
│   └── tests/                # 单元测试
├── java-service/             # Java 数据服务（占位）
└── docs/                     # 架构 / 面试 / 技术详解文档
```

---

## 四、快速启动

### 方式一：Docker Compose（推荐）

```bash
# 1. 准备配置
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY

# 2. 一键启动（Redis + 引擎）
docker compose up -d

# 3. 健康检查
curl http://localhost:8000/health
```

### 方式二：本地开发

```bash
cd engine
# 安装依赖（推荐 uv）
uv pip install -r pyproject.toml
# 或：pip install -e .

# 启动（需本地已运行 Redis）
python -m engine.main
```

访问 `http://localhost:8000/docs` 查看接口文档。

---

> 本 README 为框架版，随各功能模块落地会持续补充实现细节与面试话术。
