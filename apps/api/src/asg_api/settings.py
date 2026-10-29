"""应用配置（pydantic-settings，环境变量 ASG_ 前缀）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASG_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/ashare_lab.db"
    artifact_root: str = "./data/artifacts"

    llm_provider: str = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_budget_cny: float = 0.0

    api_host: str = "127.0.0.1"
    api_port: int = 8300
    secret_key: str = "change-me-to-a-long-random-string"
    access_token_expire_minutes: int = 720
    rate_limit_per_minute: int = 120

    allow_real_trading: bool = False  # 永久 False；硬约束，任何代码路径不得开启真实交易
    allow_remote_fetch: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
