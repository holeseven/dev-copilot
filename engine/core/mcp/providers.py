# @author lxy
"""
ToolProvider —— 工具提供者抽象与实现

定义工具提供者的统一接口，以及两个开箱即用的实现：
- MockToolProvider: 返回假数据，用于 demo 和测试。
- HttpToolProvider: 通过 httpx.AsyncClient 调用远程 MCP Server。
"""
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class ToolProvider(ABC):
    """
    工具提供者抽象基类。

    所有 MCP 工具源（本地、远程、Mock）均需实现此接口，
    以便 MCPManager 统一管理。
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        列出该 Provider 提供的所有工具。

        Returns:
            工具描述列表，每项至少含 name / description / parameters 字段。
        """
        ...

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        调用指定工具。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。
        Returns:
            工具执行结果。
        Raises:
            Exception: 调用失败时抛出。
        """
        ...


# ──────────────────────────── Mock 实现 ────────────────────────────


class MockToolProvider(ToolProvider):
    """
    Mock 工具提供者，用于 demo 演示和单元测试。

    提供 echo / timestamp 两个示例工具，始终返回确定性假数据。
    """

    MOCK_TOOLS = [
        {
            "name": "echo",
            "description": "回显输入内容（Mock）",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "要回显的消息"},
                },
                "required": ["message"],
            },
        },
        {
            "name": "timestamp",
            "description": "返回当前时间戳（Mock 固定值）",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    async def list_tools(self) -> List[Dict[str, Any]]:
        """返回预定义的 Mock 工具列表"""
        logger.debug(f"[MockToolProvider:{self.name}] list_tools called")
        return self.MOCK_TOOLS

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        执行 Mock 工具调用。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。
        Returns:
            Mock 结果。
        """
        logger.debug(f"[MockToolProvider:{self.name}] call_tool: {tool_name}")
        if tool_name == "echo":
            return {"result": arguments.get("message", ""), "mock": True}
        elif tool_name == "timestamp":
            return {"timestamp": "2025-01-01T00:00:00Z", "mock": True}
        else:
            return {"error": f"未知工具: {tool_name}", "mock": True}


# ──────────────────────────── HTTP 实现 ────────────────────────────


class HttpToolProvider(ToolProvider):
    """
    HTTP 工具提供者，通过 httpx.AsyncClient 调用远程 MCP Server。

    遵循 MCP 协议：
    - GET  /tools/list          → 获取工具列表
    - POST /tools/call          → 调用工具
    """

    def __init__(self, name: str, base_url: str, timeout: float = 30.0):
        """
        Args:
            name: Provider 名称。
            base_url: MCP Server 基础 URL（如 http://localhost:8080）。
            timeout: 单次请求超时（秒）。
        """
        super().__init__(name)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = None

    async def _get_client(self):
        """惰性创建 httpx AsyncClient"""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
            )
        return self._client

    async def list_tools(self) -> List[Dict[str, Any]]:
        """
        从远程 MCP Server 获取工具列表。

        Returns:
            工具描述列表。
        Raises:
            httpx.HTTPStatusError: 非 2xx 响应。
        """
        client = await self._get_client()
        response = await client.get("/tools/list")
        response.raise_for_status()
        data = response.json()
        tools = data.get("tools", data) if isinstance(data, dict) else data
        logger.info(f"[HttpToolProvider:{self.name}] 获取到 {len(tools)} 个工具")
        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        调用远程 MCP Server 上的工具。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数。
        Returns:
            远程执行结果。
        Raises:
            httpx.HTTPStatusError: 非 2xx 响应。
        """
        client = await self._get_client()
        payload = {
            "name": tool_name,
            "arguments": arguments,
        }
        response = await client.post("/tools/call", json=payload)
        response.raise_for_status()
        result = response.json()
        logger.debug(f"[HttpToolProvider:{self.name}] {tool_name} 调用成功")
        return result

    async def close(self):
        """关闭底层 HTTP 连接"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
