"""Validated configuration for the Python MT5 Trading Worker."""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    worker_api_base_url: str = Field(default="http://127.0.0.1:5000")
    worker_auth_token: str = Field(min_length=16)

    redis_url: str = Field(default="redis://:change_me_redis_password@127.0.0.1:6379")
    redis_stream_commands: str = Field(default="commands:trading")
    redis_pubsub_channel: str = Field(default="events:sse")

    mt5_terminal_path: str = Field(default=r"C:\Program Files\MetaTrader 5\terminal64.exe")
    mt5_account_login: int = Field(gt=0)
    mt5_account_server: str = Field(min_length=1)
    mt5_account_mode: Literal["DEMO", "LIVE"] = Field(default="DEMO")
    mt5_account_type: Literal["STANDARD", "CENT"] = Field(default="CENT")
    mt5_symbol: str = Field(default="XAUUSDc", min_length=1)
    mt5_magic_number: int = Field(default=260101, gt=0)

    openai_api_key: str = Field(default="")
    openai_model: str = Field(default="gpt-4o-mini")
    ai_timeout_seconds: float = Field(default=5.0, gt=0.5, le=30.0)
    ai_fallback_mode: Literal["DETERMINISTIC_ONLY", "SKIP"] = Field(
        default="DETERMINISTIC_ONLY"
    )
