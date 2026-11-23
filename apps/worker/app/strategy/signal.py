"""Typed deterministic strategy input context and output candidate models."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.market.features import BlackoutWindowSpec
from app.mt5.market_data import CandleBar

TradeDirection = Literal["BUY", "SELL"]


class SetupCandidate(BaseModel):
    strategy_id: str
    strategy_name: str
    strategy_version: str
    symbol: str
    timeframe: Literal["M5", "M15", "H1"] = "M15"
    direction: TradeDirection
    order_type: Literal["MARKET"] = "MARKET"
    candle_time: datetime
    entry_reference: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    reward_risk: Decimal
    dedup_key: str
    deterministic_reasons: list[str]
    invalidation_rules: list[str]
    indicators: dict[str, Any] = Field(default_factory=dict)


class StrategyContext(BaseModel):
    symbol: str
    h1_bars: list[CandleBar]
    m15_bars: list[CandleBar]
    current_bid: Decimal
    current_ask: Decimal
    max_spread: Decimal
    now_utc: datetime
    blackouts: list[BlackoutWindowSpec] = Field(default_factory=list)
    allowed_sessions: list[str] = Field(default_factory=lambda: ["LONDON", "NEW_YORK"])
    rollover_start_utc: str = "21:00"
    rollover_end_utc: str = "22:00"
    cooldown_until: datetime | None = None
    existing_dedup_keys: set[str] = Field(default_factory=set)

    @property
    def current_spread(self) -> Decimal:
        return self.current_ask - self.current_bid


def build_dedup_key(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    candle_time: datetime,
    direction: TradeDirection,
) -> str:
    iso = candle_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{strategy_id}:{symbol}:{timeframe}:{iso}:{direction}"
