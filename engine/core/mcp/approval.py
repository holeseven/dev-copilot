# @author lxy
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    """Server-issued approval descriptor for one prepared sensitive tool call."""

    approval_id: str
    action: str
    payload: Mapping[str, Any]
