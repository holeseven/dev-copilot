import pytest

from engine.core.dispatcher.config import AgentCandidate, DispatcherConfig
from engine.core.dispatcher.dispatcher import AgentDispatcher
from engine.core.dispatcher.models import RoutingLLMOutput


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
