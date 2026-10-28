# @author lxy
"""
全局配置模块

使用 Pydantic Settings 从环境变量 / .env 文件读取配置。
所有配置项集中在此，方便统一管理与热更新对接。
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置项（从环境变量或 .env 读取）"""

    # ===== LLM 配置（OpenAI 兼容协议：DeepSeek / 通义千问 等均可）=====
    llm_api_key: str = Field(default="", description="LLM API Key")
    llm_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="LLM 服务地址（OpenAI 兼容）",
    )
    # 默认模型（L1 简单任务用的轻量模型）
    llm_model: str = Field(default="deepseek-chat", description="默认/L1 模型名")
    # L2 复杂任务用的更强模型（不配则回落到 llm_model）
    llm_model_l2: str = Field(default="", description="L2 复杂任务模型名，为空则复用默认模型")

    # LLM 通用参数
    llm_temperature: float = Field(default=0.7, description="采样温度")
    llm_timeout: int = Field(default=60, description="单次调用超时（秒）")

    # ===== Redis 配置 =====
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis 连接地址")
    redis_max_connections: int = Field(default=20, description="连接池最大连接数")

    # ===== 运行时配置 =====
    # Agent 可操作的工作区根目录（文件类 MCP 工具会限制在此目录内，防越界）
    workspace_dir: str = Field(default="./workspace", description="Agent 工作区根目录")
    workspace_metadata_dir: str = Field(
        default="~/.dev-copilot",
        description="工作区注册表等本地元数据目录",
    )
    checkpoint_db_path: str | None = Field(default=None)
    proposal_db_path: str | None = Field(default=None)
    rag_db_path: str | None = Field(default=None)
    workspace_allowed_roots: str | None = Field(
        default=None,
        description="允许注册的工作区根目录（JSON 数组或系统路径分隔符分隔）",
    )
    workspace_write_enabled: bool = Field(
        default=False,
        description="显式启用受控 Workspace 写入；默认只暴露只读工具",
    )
    # 动态配置在 Redis 中存放的 Hash key
    dynamic_config_key: str = Field(default="dev_copilot:config", description="动态配置 Hash key")
    # 动态配置本地缓存刷新间隔（秒）
    config_refresh_interval: int = Field(default=30, description="配置热更新轮询间隔（秒）")

    # ===== 服务配置 =====
    app_name: str = Field(default="dev-copilot-engine", description="应用名")
    app_host: str = Field(default="127.0.0.1", description="监听地址")
    app_port: int = Field(default=8000, description="监听端口")
    log_level: str = Field(default="INFO", description="日志级别")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未声明的多余环境变量
    )

    @property
    def workspace_metadata_path(self) -> Path:
        """Return the canonical local metadata directory without creating it."""

        return Path(self.workspace_metadata_dir).expanduser().resolve()

    @property
    def resolved_checkpoint_db_path(self) -> Path:
        if self.checkpoint_db_path:
            return Path(self.checkpoint_db_path).expanduser().resolve()
        return self.workspace_metadata_path / "checkpoints.sqlite3"

    @property
    def resolved_proposal_db_path(self) -> Path:
        if self.proposal_db_path:
            return Path(self.proposal_db_path).expanduser().resolve()
        return self.workspace_metadata_path / "proposals.sqlite3"

    @property
    def resolved_rag_db_path(self) -> Path:
        if self.rag_db_path:
            return Path(self.rag_db_path).expanduser().resolve()
        return self.workspace_metadata_path / "rag.sqlite3"

    @property
    def workspace_allowed_root_paths(self) -> tuple[Path, ...]:
        """Return canonical directory boundaries for workspace registration.

        The legacy ``workspace_dir`` remains the safe default. It is not required
        to exist at settings-load time because existing deployments may create it
        during application startup. Explicit allow-list entries fail closed.
        """

        if self.workspace_allowed_roots is None:
            return (Path(self.workspace_dir).expanduser().resolve(),)
        return self._parse_explicit_allowed_roots(self.workspace_allowed_roots)

    @staticmethod
    def _parse_explicit_allowed_roots(raw: str) -> tuple[Path, ...]:
        value = raw.strip()
        if not value:
            raise ValueError("workspace_allowed_roots must contain at least one directory")

        if value.startswith("["):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("workspace_allowed_roots contains invalid JSON") from exc
            if not isinstance(decoded, list):
                raise ValueError("workspace_allowed_roots JSON value must be an array")
            entries = decoded
        else:
            entries = value.split(os.pathsep)

        if not entries:
            raise ValueError("workspace_allowed_roots must contain at least one directory")
        if any(not isinstance(entry, str) for entry in entries):
            raise ValueError("workspace_allowed_roots must contain only directory strings")
        if any(not entry.strip() for entry in entries):
            raise ValueError("workspace_allowed_roots must contain at least one directory")

        roots: list[Path] = []
        for entry in entries:
            root = Path(entry.strip()).expanduser().resolve()
            if not root.exists():
                raise ValueError(f"workspace allowed root does not exist: {entry}")
            if not root.is_dir():
                raise ValueError(f"workspace allowed root is not a directory: {entry}")
            if root not in roots:
                roots.append(root)
        return tuple(roots)

    @model_validator(mode="after")
    def validate_workspace_allowed_roots(self) -> "Settings":
        if self.workspace_allowed_roots is not None:
            self._parse_explicit_allowed_roots(self.workspace_allowed_roots)
        return self


@lru_cache
def get_settings() -> Settings:
    """获取全局唯一配置实例（带缓存，避免重复解析 .env）"""
    return Settings()
