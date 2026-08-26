# @author lxy
"""
子 Agent 工具封装

将业务 Agent 包装为 LangChain @tool，使 Supervisor 可以通过工具调用方式委派任务。

核心设计：
- 每个业务 Agent 被包装为名为 `call_{agent_id}` 的 tool
- Supervisor 通过 HTTP Bridge 模式（httpx）委派任务到子 Agent 服务
- 每次调用携带 parent_step_id 实现父子会话追踪
- 支持超时控制和错误重试
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional

import httpx
from langchain_core.tools import StructuredTool, tool
from loguru import logger

from engine.config.settings import get_settings

# ─── 子 Agent 注册表 ──────────────────────────────────────────────────────────

# agent_id → endpoint 映射（后续可从配置中心动态加载）
_AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "knowledge_agent": {
        "name": "知识查询 Agent",
        "endpoint": "/api/agents/knowledge/invoke",
        "description": "处理知识检索、文档查询、概念解释等任务",
        "timeout": 30,
    },
    "task_agent": {
        "name": "任务执行 Agent",
        "endpoint": "/api/agents/task/invoke",
        "description": "处理工单创建、状态变更、部署发布等操作类任务",
        "timeout": 60,
    },
    "code_agent": {
        "name": "代码 Agent",
        "endpoint": "/api/agents/code/invoke",
        "description": "处理代码生成、审查、重构等开发类任务",
        "timeout": 120,
    },
    "qa_agent": {
        "name": "问答 Agent",
        "endpoint": "/api/agents/qa/invoke",
        "description": "处理通用问答、闲聊、意见反馈等轻量任务",
        "timeout": 15,
    },
}


class SubAgentBridge:
    """
    子 Agent HTTP Bridge

    通过 httpx 异步 HTTP 调用委派任务到子 Agent 微服务，
    每次调用注入 parent_step_id 实现全链路追踪。
    """

    def __init__(self, agent_id: str, config: Dict[str, Any]) -> None:
        self.agent_id = agent_id
        self.config = config
        self._endpoint = config["endpoint"]
        self._timeout = config.get("timeout", 30)

    async def invoke(
        self,
        task_input: str,
        parent_step_id: Optional[str] = None,
    ) -> str:
        """
        委派任务到子 Agent

        :param task_input: 任务描述/输入
        :param parent_step_id: 父步骤 ID（用于链路追踪）
        :return: 子 Agent 执行结果
        """
        step_id = str(uuid.uuid4())
        parent_step_id = parent_step_id or "root"

        settings = get_settings()
        base_url = f"http://{settings.app_host}:{settings.app_port}"

        payload = {
            "input": task_input,
            "metadata": {
                "step_id": step_id,
                "parent_step_id": parent_step_id,
                "agent_id": self.agent_id,
            },
        }

        logger.info(
            "SubAgent 委派 | agent={} | step={} | parent={}",
            self.agent_id, step_id, parent_step_id,
        )

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{base_url}{self._endpoint}",
                    json=payload,
                    headers={
                        "X-Step-Id": step_id,
                        "X-Parent-Step-Id": parent_step_id,
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                result = response.json()
                output = result.get("output", result.get("content", str(result)))

                logger.info(
                    "SubAgent 返回 | agent={} | step={} | output_len={}",
                    self.agent_id, step_id, len(str(output)),
                )
                return str(output)

        except httpx.TimeoutException:
            error_msg = f"子 Agent {self.agent_id} 调用超时 ({self._timeout}s)"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        except httpx.HTTPStatusError as exc:
            error_msg = f"子 Agent {self.agent_id} 返回错误: HTTP {exc.response.status_code}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        except Exception as exc:
            error_msg = f"子 Agent {self.agent_id} 调用异常: {str(exc)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)


def _extract_parent_step_id(task_input: str) -> tuple[str, str]:
    """从任务输入中提取 parent_step_id（如有）"""
    match = re.match(r"\[parent_step_id=([^\]]+)\]\n?(.*)", task_input, re.DOTALL)
    if match:
        return match.group(1), match.group(2)
    return "root", task_input


def _create_agent_tool(agent_id: str, config: Dict[str, Any]) -> StructuredTool:
    """为指定 Agent 创建 LangChain StructuredTool"""
    bridge = SubAgentBridge(agent_id, config)

    async def _invoke_agent(task_input: str) -> str:
        """委派任务到子 Agent 并返回结果"""
        parent_step_id, clean_input = _extract_parent_step_id(task_input)
        return await bridge.invoke(clean_input, parent_step_id)

    return StructuredTool.from_function(
        coroutine=_invoke_agent,
        name=f"call_{agent_id}",
        description=config["description"],
    )


def build_subagent_tools() -> List[StructuredTool]:
    """
    构建所有子 Agent 工具列表

    遍历注册表，为每个 Agent 创建对应的 @tool 实例，
    供 Supervisor 作为工具集使用。

    :return: StructuredTool 列表
    """
    tools = []
    for agent_id, config in _AGENT_REGISTRY.items():
        tool_instance = _create_agent_tool(agent_id, config)
        tools.append(tool_instance)
        logger.debug("注册子 Agent 工具: call_{}", agent_id)

    logger.info("子 Agent 工具集构建完成 | 总数={}", len(tools))
    return tools


def get_agent_registry() -> Dict[str, Dict[str, Any]]:
    """获取 Agent 注册表（只读）"""
    return dict(_AGENT_REGISTRY)
