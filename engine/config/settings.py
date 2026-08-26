# @author lxy
"""
全局配置模块

使用 Pydantic Settings 从环境变量 / .env 文件读取配置。
所有配置项集中在此，方便统一管理与热更新对接。
"""
from functools import lru_cache

from pydantic import Field
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
    # 动态配置在 Redis 中存放的 Hash key
    dynamic_config_key: str = Field(default="dev_copilot:config", description="动态配置 Hash key")
    # 动态配置本地缓存刷新间隔（秒）
    config_refresh_interval: int = Field(default=30, description="配置热更新轮询间隔（秒）")

    # ===== 服务配置 =====
    app_name: str = Field(default="dev-copilot-engine", description="应用名")
    app_host: str = Field(default="0.0.0.0", description="监听地址")
    app_port: int = Field(default=8000, description="监听端口")
    log_level: str = Field(default="INFO", description="日志级别")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未声明的多余环境变量
    )


@lru_cache
def get_settings() -> Settings:
    """获取全局唯一配置实例（带缓存，避免重复解析 .env）"""
    return Settings()
