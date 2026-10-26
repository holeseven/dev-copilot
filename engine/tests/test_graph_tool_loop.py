from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from engine.core.mcp.local_provider import LocalDevToolProvider
from engine.core.mcp.manager import MCPManager
from engine.core.workspace.models import Repository, Workspace
from engine.core.workspace.path_guard import WorkspacePathGuard
from engine.orchestrator.graph_builder import build_agent_graph
from engine.orchestrator.nodes import agent_node, tool_node


class FakeDispatcher:
    async def route(self, query):
        return SimpleNamespace(target_agent_id="knowledge_agent", complexity_level="L1")


class TwoStageLLM:
    def __init__(self):
        self.bound_tools = []
        self.calls = 0

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"path": "orders/README.md"},
                        "id": "read-orders",
                        "type": "tool_call",
                    },
                    {
                        "name": "read_file",
                        "args": {"path": "platform/settings.py"},
                        "id": "read-settings",
                        "type": "tool_call",
                    },
                ],
            )
        tool_text = "\n".join(
            str(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
        )
        return AIMessage(content=f"Found order-service and {tool_text.strip()}")


class OneStageLLM:
    def bind_tools(self, tools):
        raise AssertionError("tools must not be bound without a manager")

    async def ainvoke(self, messages):
        return AIMessage(content="no tools available")


def _initial_state():
    return {
        "messages": [],
        "session_id": "session-1",
        "user_query": "读取两个仓库并总结重试配置",
        "target_agent_id": "",
        "complexity_level": "L2",
        "iteration_count": 0,
        "max_iterations": 35,
        "should_terminate": False,
        "tool_results": [],
    }


@pytest.mark.asyncio
async def test_graph_runs_real_read_file_loop_across_two_repositories(tmp_path, monkeypatch):
    orders = tmp_path / "orders"
    platform = tmp_path / "platform"
    orders.mkdir()
    platform.mkdir()
    (orders / "README.md").write_text("service: order-service\n", encoding="utf-8")
    (platform / "settings.py").write_text("MAX_RETRY = 3\n", encoding="utf-8")
    workspace = Workspace(
        id="services",
        name="Services",
        repositories=[
            Repository(name="orders", path=str(orders)),
            Repository(name="platform", path=str(platform)),
        ],
    )
    manager = MCPManager(
        [
            {
                "name": "local",
                "type": "local",
                "provider": LocalDevToolProvider(
                    "local",
                    workspace,
                    path_guard=WorkspacePathGuard.from_roots((tmp_path,)),
                ),
            }
        ]
    )
    await manager.init_all()
    llm = TwoStageLLM()
    monkeypatch.setattr("engine.orchestrator.nodes.get_llm_by_complexity", lambda _: llm)

    try:
        result = await build_agent_graph().ainvoke(
            _initial_state(),
            config={
                "recursion_limit": 150,
                "configurable": {
                    "workspace_id": "services",
                    "session_id": "session-1",
                    "mcp_manager": manager,
                    "dispatcher": FakeDispatcher(),
                },
            },
        )
    finally:
        await manager.shutdown()

    assert [tool["function"]["name"] for tool in llm.bound_tools] == [
        "list_dir",
        "find_files",
        "grep_code",
        "read_file",
        "code_search",
    ]
    assert llm.calls == 2
    assert sum(isinstance(message, ToolMessage) for message in result["messages"]) == 2
    assert "order-service" in result["messages"][-1].content
    assert "MAX_RETRY = 3" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_workspace_bound_manager_cannot_read_another_workspace_path(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "secret.txt").write_text("first only\n", encoding="utf-8")
    first = Workspace(
        id="first",
        name="First",
        repositories=[Repository(name="first-repo", path=str(first_root))],
    )
    second = Workspace(
        id="second",
        name="Second",
        repositories=[Repository(name="second-repo", path=str(second_root))],
    )
    manager = MCPManager(
        [
            {
                "name": "local",
                "type": "local",
                "provider": LocalDevToolProvider(
                    "local",
                    second,
                    path_guard=WorkspacePathGuard.from_roots((second_root,)),
                ),
            }
        ]
    )
    await manager.init_all()

    try:
        result = await manager.call_tool(
            "read_file", {"path": f"{first.repositories[0].name}/secret.txt"}
        )
    finally:
        await manager.shutdown()

    assert result.startswith("[ERROR]")
    assert "outside the virtual workspace" in result


@pytest.mark.asyncio
async def test_agent_does_not_expose_tools_without_a_manager(monkeypatch):
    llm = OneStageLLM()
    monkeypatch.setattr("engine.orchestrator.nodes.get_llm_by_complexity", lambda _: llm)

    result = await agent_node(
        {
            "messages": [],
            "complexity_level": "L1",
            "iteration_count": 0,
        },
        config={"configurable": {"mcp_manager": None}},
    )

    assert result["messages"][-1].content == "no tools available"


@pytest.mark.asyncio
async def test_tool_node_dispatches_custom_status_events_with_runtime_config(monkeypatch):
    events = []
    config = {"configurable": {"workspace_id": "services"}}

    class Manager:
        async def call_tool(self, name, arguments):
            return "contents"

    async def capture(name, data, *, config):
        events.append((name, data, config))

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", capture)

    await tool_node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "repo/file.txt"},
                            "id": "read-one",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        config={"configurable": {**config["configurable"], "mcp_manager": Manager()}},
    )

    assert [(name, data) for name, data, _ in events] == [
        ("tool_status", {"status": "start", "tool_name": "read_file"}),
        ("tool_status", {"status": "end", "tool_name": "read_file"}),
    ]
    assert all(event_config["configurable"]["workspace_id"] == "services" for _, _, event_config in events)


@pytest.mark.asyncio
async def test_tool_node_dispatches_error_status_for_failed_call(monkeypatch):
    events = []

    class Manager:
        async def call_tool(self, name, arguments):
            return "[ERROR] file not found"

    async def capture(name, data, *, config):
        events.append(data)

    monkeypatch.setattr("engine.orchestrator.nodes.adispatch_custom_event", capture)

    result = await tool_node(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "repo/missing.txt"},
                            "id": "read-missing",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        },
        config={"configurable": {"mcp_manager": Manager()}},
    )

    assert events == [
        {"status": "start", "tool_name": "read_file"},
        {"status": "error", "tool_name": "read_file"},
    ]
    assert result["tool_results"][0]["status"] == "error"
