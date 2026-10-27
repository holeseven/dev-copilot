# @author lxy
from io import StringIO

import pytest
from langchain_core.messages import AIMessage
from loguru import logger

from engine.agents.qa_agent import QAAgent
from engine.agents.task_agent import TaskAgent


@pytest.mark.asyncio
async def test_qa_agent_run_failure_is_sanitized(monkeypatch):
    secret_error = "qa provider leaked sk-secret at C:/private/qa"
    captured_logs = StringIO()
    sink_id = logger.add(captured_logs, format="{message}")

    class FailingLlm:
        async def ainvoke(self, messages):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        "engine.agents.qa_agent.get_llm_by_complexity",
        lambda _: FailingLlm(),
    )
    agent = QAAgent()

    try:
        answer = await agent.run("hello")
    finally:
        logger.remove(sink_id)

    assert answer == "回答过程中出现错误，请稍后重试。"
    assert secret_error not in answer
    assert secret_error not in captured_logs.getvalue()


@pytest.mark.asyncio
async def test_qa_agent_stream_failure_is_sanitized(monkeypatch):
    secret_error = "qa stream leaked sk-secret at C:/private/qa"
    captured_logs = StringIO()
    sink_id = logger.add(captured_logs, format="{message}")

    class FailingLlm:
        async def astream(self, messages):
            raise RuntimeError(secret_error)
            yield

    monkeypatch.setattr(
        "engine.agents.qa_agent.get_llm_by_complexity",
        lambda _: FailingLlm(),
    )
    agent = QAAgent()

    try:
        chunks = [chunk async for chunk in agent.stream_run("hello")]
    finally:
        logger.remove(sink_id)

    assert chunks == ["回答过程中出现错误，请稍后重试。"]
    assert secret_error not in "".join(chunks)
    assert secret_error not in captured_logs.getvalue()


@pytest.mark.asyncio
async def test_task_agent_model_failure_is_sanitized(monkeypatch):
    secret_error = "task provider leaked sk-secret at C:/private/task"
    captured_logs = StringIO()
    sink_id = logger.add(captured_logs, format="{message}")

    class FailingLlm:
        async def ainvoke(self, messages):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        "engine.agents.task_agent.get_llm_by_complexity",
        lambda _: FailingLlm(),
    )
    agent = TaskAgent()

    try:
        answer = await agent.run("inspect the project")
    finally:
        logger.remove(sink_id)

    assert answer == "任务执行过程中出现错误，请稍后重试。"
    assert secret_error not in answer
    assert secret_error not in captured_logs.getvalue()


@pytest.mark.asyncio
async def test_task_agent_tool_failure_is_sanitized(monkeypatch):
    secret_error = "tool leaked sk-secret at C:/private/tool"

    class UnusedLlm:
        async def ainvoke(self, messages):
            return AIMessage(content="unused")

    class FailingManager:
        def get_tool_schemas(self):
            return []

        async def call_tool(self, tool_name, arguments):
            raise RuntimeError(secret_error)

    monkeypatch.setattr(
        "engine.agents.task_agent.get_llm_by_complexity",
        lambda _: UnusedLlm(),
    )
    agent = TaskAgent(mcp_manager=FailingManager())

    results = await agent._execute_tool_calls(
        [{"name": "read_file", "args": {"path": "repo/file.py"}, "id": "call-1"}]
    )

    assert results[0].content == "[ERROR] 工具执行失败。"
    assert secret_error not in str(results[0].content)
