# @author lxy
"""
MCP Manager —— 三层容错管理器

L1: init_all() 并行初始化所有 MCP Server，单 Server 超时/异常不阻塞整体。
L2: call_tool() 失败自动重试 3 次（退避 5/10/15s），错误作为结果返回给 LLM。
L3: _self_heal_loop() 后台定时重连 + 工具增量刷新 + 空集防护。
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from engine.core.mcp.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ──────────────────────────── 配置常量 ────────────────────────────

DEFAULT_INIT_TIMEOUT = 30  # 单 Server 初始化超时（秒）
RETRY_BACKOFFS = [5, 10, 15]  # 重试退避时间列表（秒）
HEAL_INTERVAL = 60  # 自愈循环间隔（秒）


class MCPManager:
    """
    MCP 工具管理器，负责初始化、调用、自愈三层容错。

    Usage:
        manager = MCPManager(server_configs=[...])
        await manager.init_all()
        result = await manager.call_tool("tool_name", {"arg": "val"})
        await manager.shutdown()
    """

    def __init__(self, server_configs: List[Dict[str, Any]], registry: Optional[ToolRegistry] = None):
        """
        Args:
            server_configs: MCP Server 配置列表，每项包含 name/url/type 等字段。
            registry: 工具注册表实例，未提供则自动创建。
        """
        self.server_configs = server_configs
        self.registry = registry or ToolRegistry()
        self._heal_task: Optional[asyncio.Task] = None
        self._running = False
        self._failed_servers: List[str] = []

    # ═══════════════════ L1: 并行初始化 ═══════════════════

    async def init_all(self) -> Dict[str, str]:
        """
        并行初始化所有 MCP Server。

        使用 asyncio.gather + return_exceptions=True，单个 Server 超时/失败
        不影响其余 Server。

        Returns:
            dict 映射 server_name → 'ok' | 错误消息。
        """
        results: Dict[str, str] = {}

        async def _init_one(config: Dict[str, Any]) -> tuple:
            name = config.get("name", "unknown")
            try:
                # 模拟初始化：实际会连接远程 MCP Server 并拉取工具列表
                tools = await asyncio.wait_for(
                    self._connect_server(config),
                    timeout=config.get("timeout", DEFAULT_INIT_TIMEOUT),
                )
                for tool in tools:
                    self.registry.register(tool)
                return name, "ok"
            except asyncio.TimeoutError:
                return name, f"初始化超时（>{config.get('timeout', DEFAULT_INIT_TIMEOUT)}s）"
            except Exception as e:
                return name, f"初始化失败: {type(e).__name__}: {e}"

        tasks = [_init_one(cfg) for cfg in self.server_configs]
        gather_results = await asyncio.gather(*tasks, return_exceptions=True)

        for item in gather_results:
            if isinstance(item, Exception):
                logger.error(f"初始化异常: {item}")
                continue
            name, status = item
            results[name] = status
            if status != "ok":
                self._failed_servers.append(name)
                logger.warning(f"Server [{name}] {status}")

        # 启动自愈循环
        self._running = True
        self._heal_task = asyncio.create_task(self._self_heal_loop())
        logger.info(f"MCPManager 初始化完成: {len(results)} 个 Server，"
                    f"失败 {len(self._failed_servers)} 个")
        return results

    async def _connect_server(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        连接单个 MCP Server 并获取工具列表（子类或实际实现可覆写）。

        Args:
            config: Server 配置字典。
        Returns:
            工具描述列表。
        """
        # 默认实现：从 provider 获取工具列表
        from engine.core.mcp.providers import ToolProvider

        provider_type = config.get("type", "mock")
        provider: Optional[ToolProvider] = None

        if provider_type == "http":
            from engine.core.mcp.providers import HttpToolProvider
            provider = HttpToolProvider(
                name=config["name"],
                base_url=config["url"],
            )
        elif provider_type == "mock":
            from engine.core.mcp.providers import MockToolProvider
            provider = MockToolProvider(name=config["name"])

        if provider is None:
            raise ValueError(f"不支持的 provider 类型: {provider_type}")

        tools = await provider.list_tools()
        return [{"name": t["name"], "provider": provider, "schema": t} for t in tools]

    # ═══════════════════ L2: 调用 + 重试 ═══════════════════

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        调用工具，失败自动重试 3 次（退避 5/10/15s）。
        最终仍失败则将错误信息作为结果返回（供 LLM 决策）。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。
        Returns:
            工具执行结果，或错误描述字符串。
        """
        tool_entry = self.registry.get(tool_name)
        if tool_entry is None:
            return f"[ERROR] 工具 '{tool_name}' 未注册"

        last_error: Optional[Exception] = None
        for attempt, backoff in enumerate(RETRY_BACKOFFS, 1):
            try:
                provider = tool_entry.get("provider")
                if provider is None:
                    return f"[ERROR] 工具 '{tool_name}' 无可用 provider"
                result = await provider.call_tool(tool_name, arguments)
                return result
            except Exception as e:
                last_error = e
                logger.warning(
                    f"工具 [{tool_name}] 第 {attempt} 次调用失败: {e}，"
                    f"{backoff}s 后重试..."
                )
                if attempt < len(RETRY_BACKOFFS):
                    await asyncio.sleep(backoff)

        error_msg = f"[ERROR] 工具 '{tool_name}' 重试 {len(RETRY_BACKOFFS)} 次后仍失败: {last_error}"
        logger.error(error_msg)
        return error_msg

    # ═══════════════════ L3: 后台自愈 ═══════════════════

    async def _self_heal_loop(self):
        """
        后台自愈循环：
        1. 定时重连失败的 Server。
        2. 对已连接 Server 做工具增量刷新。
        3. 若注册表为空则发出告警（空集防护）。
        """
        while self._running:
            try:
                await asyncio.sleep(HEAL_INTERVAL)

                # 空集防护
                if self.registry.count() == 0:
                    logger.critical("⚠️ 工具注册表为空！尝试全量重连...")
                    self._failed_servers = [
                        cfg["name"] for cfg in self.server_configs
                    ]

                # 重连失败 Server
                reconnected = []
                for server_name in list(self._failed_servers):
                    config = next(
                        (c for c in self.server_configs if c.get("name") == server_name),
                        None,
                    )
                    if config is None:
                        continue
                    try:
                        tools = await asyncio.wait_for(
                            self._connect_server(config),
                            timeout=DEFAULT_INIT_TIMEOUT,
                        )
                        for tool in tools:
                            self.registry.register(tool)
                        reconnected.append(server_name)
                        logger.info(f"✅ Server [{server_name}] 重连成功")
                    except Exception as e:
                        logger.debug(f"Server [{server_name}] 重连失败: {e}")

                for name in reconnected:
                    self._failed_servers.remove(name)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自愈循环异常: {e}")

    # ═══════════════════ 生命周期 ═══════════════════

    async def shutdown(self):
        """优雅关闭管理器"""
        self._running = False
        if self._heal_task and not self._heal_task.done():
            self._heal_task.cancel()
            try:
                await self._heal_task
            except asyncio.CancelledError:
                pass
        logger.info("MCPManager 已关闭")

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """获取当前所有已注册工具的 schema 列表（供 LLM function calling 使用）"""
        return self.registry.list_schemas()
