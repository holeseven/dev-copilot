# @author lxy
"""
ToolRegistry —— 工具注册表

维护 name → ToolEntry 映射，支持注册、查找、去重、列表等操作。
同名工具注册时跳过（去重），避免多 Server 提供重复工具导致冲突。
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ToolEntry:
    """单个工具的注册条目"""

    def __init__(self, name: str, provider: Any, schema: Dict[str, Any]):
        self.name = name
        self.provider = provider
        self.schema = schema

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "schema": self.schema,
        }


class ToolRegistry:
    """
    工具注册中心。

    功能：
    - register(): 注册工具（同名跳过去重）。
    - get(): 按名查找工具。
    - unregister(): 移除工具。
    - list_schemas(): 列出所有工具 schema（供 LLM）。
    - count(): 当前注册工具数量。
    """

    def __init__(self):
        self._tools: Dict[str, ToolEntry] = {}

    def register(self, tool_info: Dict[str, Any]) -> bool:
        """
        注册一个工具。同名工具已存在则跳过（去重）。

        Args:
            tool_info: 包含 name / provider / schema 的字典。
        Returns:
            True 表示注册成功，False 表示同名跳过。
        """
        name = tool_info.get("name", "")
        if not name:
            logger.warning("工具注册失败: 缺少 name 字段")
            return False

        if name in self._tools:
            logger.debug(f"工具 [{name}] 已存在，跳过重复注册")
            return False

        entry = ToolEntry(
            name=name,
            provider=tool_info.get("provider"),
            schema=tool_info.get("schema", {}),
        )
        self._tools[name] = entry
        logger.info(f"工具 [{name}] 注册成功")
        return True

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """
        按名称查找工具。

        Args:
            name: 工具名称。
        Returns:
            工具字典（含 name/provider/schema），未找到返回 None。
        """
        entry = self._tools.get(name)
        if entry is None:
            return None
        return entry.to_dict()

    def unregister(self, name: str) -> bool:
        """
        移除指定工具。

        Args:
            name: 工具名称。
        Returns:
            True 表示移除成功，False 表示不存在。
        """
        if name in self._tools:
            del self._tools[name]
            logger.info(f"工具 [{name}] 已移除")
            return True
        return False

    def list_schemas(self) -> List[Dict[str, Any]]:
        """
        列出所有已注册工具的 schema（供 LLM function calling 使用）。

        Returns:
            schema 列表。
        """
        return [entry.schema for entry in self._tools.values()]

    def list_names(self) -> List[str]:
        """列出所有已注册工具名称"""
        return list(self._tools.keys())

    def count(self) -> int:
        """当前注册工具总数"""
        return len(self._tools)

    def clear(self):
        """清空注册表"""
        self._tools.clear()
        logger.info("工具注册表已清空")
