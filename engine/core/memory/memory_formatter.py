"""
Memory Formatter - 三层截断 + 格式化，将原始对话记忆转换为 LLM 可读的消息列表。
# @author lxy

截断策略（三层防护）：
1. 轮次截断：仅保留最近 last_n 轮对话
2. 单条截断：question ≤ 200 字符，answer ≤ 500 字符
3. 总量兜底：格式化后总字符数 ≤ 2000 字符，超出则从最早轮次开始丢弃
"""

from typing import List, Dict, Any, Optional


# 截断阈值常量
MAX_QUESTION_LENGTH = 200
MAX_ANSWER_LENGTH = 500
MAX_TOTAL_LENGTH = 2000
DEFAULT_LAST_N = 10


class MemoryFormatter:
    """
    将原始对话记忆记录格式化为标准消息列表。

    输出格式: [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]

    三层截断保证上下文窗口不会被历史记忆撑爆：
    - 第一层：轮次限制（last_n）
    - 第二层：单条内容长度限制（question ≤ 200, answer ≤ 500）
    - 第三层：总输出字符数兜底（≤ 2000）
    """

    def __init__(
        self,
        max_question_len: int = MAX_QUESTION_LENGTH,
        max_answer_len: int = MAX_ANSWER_LENGTH,
        max_total_len: int = MAX_TOTAL_LENGTH
    ):
        """
        初始化格式化器。

        Args:
            max_question_len: 单条问题最大字符数，默认 200
            max_answer_len: 单条回答最大字符数，默认 500
            max_total_len: 总输出最大字符数，默认 2000
        """
        self.max_question_len = max_question_len
        self.max_answer_len = max_answer_len
        self.max_total_len = max_total_len

    def format(
        self,
        records: List[Dict[str, Any]],
        last_n: int = DEFAULT_LAST_N
    ) -> List[Dict[str, str]]:
        """
        将原始对话记录格式化为标准消息列表。

        Args:
            records: 原始对话记录列表，每条格式 {"q": ..., "a": ..., "ts": ...}
                     应已按时间正序排列
            last_n: 保留最近多少轮，默认 10

        Returns:
            格式化后的消息列表:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        if not records:
            return []

        # 第一层截断：仅保留最近 last_n 轮
        truncated_records = records[-last_n:] if len(records) > last_n else records

        # 第二层截断：单条内容长度限制 + 构建消息对
        messages = []
        for record in truncated_records:
            question = record.get("q", "")
            answer = record.get("a", "")

            # 截断单条 question
            truncated_q = self._truncate_text(question, self.max_question_len)
            # 截断单条 answer
            truncated_a = self._truncate_text(answer, self.max_answer_len)

            messages.append({"role": "user", "content": truncated_q})
            messages.append({"role": "assistant", "content": truncated_a})

        # 第三层兜底：总字符数限制
        messages = self._apply_total_limit(messages)

        return messages

    def _truncate_text(self, text: str, max_len: int) -> str:
        """
        截断文本到指定长度，超出部分用省略号替代。

        Args:
            text: 原始文本
            max_len: 最大允许长度

        Returns:
            截断后的文本
        """
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len - 3] + "..."

    def _apply_total_limit(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        总量兜底截断：从最早的轮次开始丢弃，直到总字符数 ≤ max_total_len。

        保证保留最近的对话，丢弃最早的对话。

        Args:
            messages: 已经过单条截断的消息列表

        Returns:
            满足总量限制的消息列表
        """
        total_len = sum(len(msg["content"]) for msg in messages)

        if total_len <= self.max_total_len:
            return messages

        # 从头部（最早的）开始每次丢弃一对（user + assistant）
        while messages and total_len > self.max_total_len:
            # 每次丢弃一对消息（保持 user/assistant 配对）
            if len(messages) >= 2:
                removed_user = messages.pop(0)
                removed_assistant = messages.pop(0)
                total_len -= len(removed_user["content"])
                total_len -= len(removed_assistant["content"])
            elif len(messages) == 1:
                removed = messages.pop(0)
                total_len -= len(removed["content"])
            else:
                break

        return messages

    def format_as_text(
        self,
        records: List[Dict[str, Any]],
        last_n: int = DEFAULT_LAST_N
    ) -> str:
        """
        将对话记录格式化为纯文本形式（用于 prompt 拼接）。

        Args:
            records: 原始对话记录列表
            last_n: 保留最近多少轮

        Returns:
            格式化后的纯文本字符串
        """
        messages = self.format(records, last_n)
        if not messages:
            return ""

        lines = []
        for msg in messages:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{role_label}: {msg['content']}")

        return "\n".join(lines)
