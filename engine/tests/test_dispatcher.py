from io import StringIO

import pytest
from langchain_core.messages import AIMessage
from loguru import logger

from engine.core.dispatcher.config import AgentCandidate, DispatcherConfig
from engine.core.dispatcher.dispatcher import AgentDispatcher
from engine.core.dispatcher.models import RoutingLLMOutput
from engine.core.streaming.sse_engine import sse_event_generator
from engine.orchestrator.graph_builder import build_agent_graph
from engine.orchestrator.nodes import preprocess_node


def make_config(timeout_ms=50):
    return DispatcherConfig(
        candidates=[
            AgentCandidate("knowledge", "Knowledge", "Finds answers"),
            AgentCandidate("fallback", "Fallback", "Handles everything else"),
        ],
        fallback_agent_id="fallback",
        timeout_ms=timeout_ms,
    )


@pytest.mark.asyncio
async def test_returns_a_structured_llm_choice_with_matching_candidate_identity(router_factory):
    from engine.tests.conftest import FakeStructuredRouter

    router_factory(FakeStructuredRouter(RoutingLLMOutput(target_agent_index=0, complexity_level="L1")))
    dispatcher = AgentDispatcher(config=make_config())

    decision = await dispatcher.route("anything")

    assert decision.target_agent_id == "knowledge"
    assert decision.target_agent_index == 0
    assert decision.complexity_level == "L1"


@pytest.mark.asyncio
async def test_uses_configured_fallback_after_a_real_timeout(router_factory):
    from engine.tests.conftest import FakeStructuredRouter

    router_factory(FakeStructuredRouter(wait_forever=True))
    dispatcher = AgentDispatcher(config=make_config(timeout_ms=1))

    decision = await dispatcher.route("anything")

    assert decision.target_agent_id == "fallback"
    assert decision.target_agent_index == 1


@pytest.mark.asyncio
async def test_uses_configured_fallback_for_out_of_range_llm_output(router_factory):
    from engine.tests.conftest import FakeStructuredRouter

    router_factory(FakeStructuredRouter(RoutingLLMOutput(target_agent_index=9)))
    dispatcher = AgentDispatcher(config=make_config())

    decision = await dispatcher.route("anything")

    assert decision.target_agent_id == "fallback"
    assert decision.target_agent_index == 1


def test_keyword_rule_for_an_unconfigured_agent_uses_the_configured_fallback(router_factory):
    from engine.tests.conftest import FakeStructuredRouter

    router_factory(FakeStructuredRouter())
    dispatcher = AgentDispatcher(config=make_config())

    decision = dispatcher._rule_based_fallback("查询订单")

    assert decision.target_agent_id == "fallback"
    assert decision.target_agent_index == 1


@pytest.mark.asyncio
async def test_router_failure_is_sanitized_and_main_model_can_complete(
    router_factory, monkeypatch
):
    secret_error = "router leaked sk-secret from C:/private/workspace"
    captured_logs = StringIO()
    sink_id = logger.add(captured_logs, format="{message}")

    class FailingStructuredRouter:
        async def ainvoke(self, messages):
            raise RuntimeError(secret_error)

    class SuccessfulMainModel:
        async def ainvoke(self, messages):
            return AIMessage(content="main model answered safely")

    router_factory(FailingStructuredRouter())
    dispatcher = AgentDispatcher(config=make_config())
    monkeypatch.setattr(
        "engine.orchestrator.nodes.get_llm_by_complexity",
        lambda _: SuccessfulMainModel(),
    )
    graph = build_agent_graph()
    initial_state = {
        "messages": [],
        "session_id": "router-fallback-session",
        "user_query": "please answer this request",
        "target_agent_id": "",
        "complexity_level": "L2",
        "iteration_count": 0,
        "max_iterations": 35,
        "should_terminate": False,
        "tool_results": [],
    }
    config = {
        "recursion_limit": 150,
        "configurable": {
            "thread_id": "router-fallback-thread",
            "session_id": "router-fallback-session",
            "dispatcher": dispatcher,
            "mcp_manager": None,
        },
    }

    try:
        frames = [
            frame
            async for frame in sse_event_generator(graph, initial_state, config)
        ]
        snapshot = await graph.aget_state(config)
    finally:
        logger.remove(sink_id)

    assert frames[-1] == 'event: done\ndata: {"status": "completed"}\n\n'
    assert all(not frame.startswith("event: error") for frame in frames)
    assert snapshot.values["target_agent_id"] == "fallback"
    assert snapshot.values["messages"][-1].content == "main model answered safely"
    assert secret_error not in "".join(frames)
    assert secret_error not in captured_logs.getvalue()


@pytest.mark.asyncio
async def test_preprocess_dispatcher_failure_uses_sanitized_fallback_log():
    secret_error = "dispatcher leaked sk-secret from C:/private/workspace"
    captured_logs = StringIO()
    sink_id = logger.add(captured_logs, format="{message}")

    class FailingDispatcher:
        async def route(self, query):
            raise RuntimeError(secret_error)

    try:
        result = await preprocess_node(
            {
                "messages": [],
                "session_id": "session-1",
                "user_query": "hello",
            },
            {"configurable": {"dispatcher": FailingDispatcher()}},
        )
    finally:
        logger.remove(sink_id)

    assert result["target_agent_id"] == "qa_agent"
    assert "code=dispatcher_unavailable" in captured_logs.getvalue()
    assert secret_error not in captured_logs.getvalue()
