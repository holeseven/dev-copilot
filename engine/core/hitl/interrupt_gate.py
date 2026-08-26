# @author lxy
"""
HITL (Human-In-The-Loop) 中断门控

利用 LangGraph 的 interrupt() 原语实现高风险操作审核：
- 在执行敏感工具（write_file / git_commit / run_command 等）前调用 interrupt() 暂停
- 等待外部通过 Command(resume=...) 恢复执行
- 封装 require_approval(action, payload) 供节点调用

使用方式：
    from engine.core.hitl.interrupt_gate import require_approval

    # 在 tool_node 中对敏感操作调用
    require_approval("write_file", {"path": "/foo/bar.py", "content": "..."})
    # 如果被批准，函数正常返回
    # 如果被拒绝，抛出 ApprovalRejectedError
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

from loguru import logger


# ─── 敏感操作集合 ──────────────────────────────────────────────────────────────────

SENSITIVE_ACTIONS: Set[str] = {
    "write_file",
    "git_commit",
    "run_command",
    "delete_file",
    "shell_exec",
    "git_push",
    "modify_config",
}


# ─── 自定义异常 ────────────────────────────────────────────────────────────────────

class ApprovalRejectedError(Exception):
    """人工审批被拒绝时抛出"""

    def __init__(self, action: str, reason: str = ""):
        self.action = action
        self.reason = reason
        super().__init__(f"操作 '{action}' 被拒绝" + (f": {reason}" if reason else ""))


class ApprovalTimeoutError(Exception):
    """人工审批超时"""

    def __init__(self, action: str, timeout_sec: int = 300):
        self.action = action
        self.timeout_sec = timeout_sec
        super().__init__(f"操作 '{action}' 审批超时（{timeout_sec}s）")


# ─── 核心门控函数 ──────────────────────────────────────────────────────────────────

def require_approval(action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    高风险操作审核门控。

    在 LangGraph 图节点内调用此函数，对敏感操作触发 interrupt 暂停。
    外部系统（前端 / API）通过 Command(resume={"approved": True/False, ...}) 恢复。

    Args:
        action: 操作名称（如 "write_file", "git_commit"）
        payload: 操作详情（如文件路径、命令内容等，供审批者查看）

    Returns:
        审批结果字典，包含 {"approved": bool, "reviewer": str, ...}

    Raises:
        ApprovalRejectedError: 如果审批被拒绝
    """
    from langgraph.types import interrupt

    if action not in SENSITIVE_ACTIONS:
        # 非敏感操作直接放行
        logger.debug("操作 '{}' 不在敏感列表中，直接放行", action)
        return {"approved": True, "action": action, "skipped": True}

    # 构造中断请求 payload
    interrupt_payload = {
        "type": "approval_required",
        "action": action,
        "payload": payload or {},
        "message": f"⚠️ 高风险操作需要人工审批: {action}",
        "details": _format_approval_details(action, payload),
    }

    logger.info("🔒 触发 HITL 中断 | action={} | payload_keys={}", action, list((payload or {}).keys()))

    # ─── 调用 LangGraph interrupt() 暂停图执行 ───
    # 外部通过 graph.invoke(None, config, command=Command(resume=...)) 恢复
    resume_value = interrupt(interrupt_payload)

    # ─── 处理恢复值 ───
    if resume_value is None:
        logger.warning("HITL 中断恢复值为 None，默认拒绝")
        raise ApprovalRejectedError(action, "未收到有效的审批响应")

    approved = resume_value.get("approved", False) if isinstance(resume_value, dict) else bool(resume_value)

    if not approved:
        reason = resume_value.get("reason", "") if isinstance(resume_value, dict) else ""
        reviewer = resume_value.get("reviewer", "unknown") if isinstance(resume_value, dict) else "unknown"
        logger.warning("❌ 操作被拒绝 | action={} | reviewer={} | reason={}", action, reviewer, reason)
        raise ApprovalRejectedError(action, reason)

    reviewer = resume_value.get("reviewer", "unknown") if isinstance(resume_value, dict) else "unknown"
    logger.info("✅ 操作已批准 | action={} | reviewer={}", action, reviewer)

    return {
        "approved": True,
        "action": action,
        "reviewer": reviewer,
        "resume_data": resume_value,
    }


# ─── 辅助：判断操作是否需要审批 ──────────────────────────────────────────────────────

def is_sensitive(action: str) -> bool:
    """
    判断给定操作是否属于敏感操作（需 HITL 审批）。

    Args:
        action: 操作/工具名称
    Returns:
        True 表示需要审批
    """
    return action in SENSITIVE_ACTIONS


def add_sensitive_action(action: str) -> None:
    """动态添加敏感操作到门控列表"""
    SENSITIVE_ACTIONS.add(action)
    logger.info("已添加敏感操作: {}", action)


def remove_sensitive_action(action: str) -> None:
    """从门控列表移除敏感操作"""
    SENSITIVE_ACTIONS.discard(action)
    logger.info("已移除敏感操作: {}", action)


# ─── 格式化审批详情 ────────────────────────────────────────────────────────────────

def _format_approval_details(action: str, payload: Optional[Dict[str, Any]]) -> str:
    """
    将操作信息格式化为人类可读的审批详情文本。

    Args:
        action: 操作名称
        payload: 操作参数

    Returns:
        格式化的详情文本
    """
    if not payload:
        return f"操作: {action}\n（无额外参数）"

    lines = [f"操作: {action}"]

    # 针对不同操作类型做友好展示
    if action == "write_file":
        path = payload.get("arguments", {}).get("path", payload.get("path", "unknown"))
        content = payload.get("arguments", {}).get("content", "")
        lines.append(f"目标文件: {path}")
        if content:
            preview = content[:200] + ("..." if len(content) > 200 else "")
            lines.append(f"内容预览:\n{preview}")

    elif action == "run_command":
        cmd = payload.get("arguments", {}).get("command", payload.get("command", "unknown"))
        cwd = payload.get("arguments", {}).get("cwd", "")
        lines.append(f"命令: {cmd}")
        if cwd:
            lines.append(f"工作目录: {cwd}")

    elif action == "git_commit":
        msg = payload.get("arguments", {}).get("message", "")
        lines.append(f"提交信息: {msg}")

    elif action == "delete_file":
        path = payload.get("arguments", {}).get("path", payload.get("path", "unknown"))
        lines.append(f"删除文件: {path}")

    else:
        # 通用格式
        for key, value in payload.items():
            lines.append(f"{key}: {value}")

    return "\n".join(lines)
