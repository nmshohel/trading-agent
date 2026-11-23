"""MT5 server-time to UTC normalization, live quote, and closed-candle readers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.mt5.client import MT5Client

# Round detected broker offset to nearest 15-minute (900s) timezone boundary
OFFSET_QUANTUM_SECONDS = 900
MAX_REASONABLE_OFFSET_SECONDS = 14 * 3600

# Official MetaTrader5 TIMEFRAME_* integer constants
TIMEFRAME_MAP: dict[str, int] = {
    "M5": 5,
    "M15": 15,
    "H1": 16385,
}

SupportedTimeframe = Literal["M5", "M15", "H1"]


class LiveQuote(BaseModel):
    symbol: str
    bid: Decimal
    ask: Decimal
    spread: Decimal
    quote_time: datetime

    def is_stale(
        self, max_age_seconds: float = 15.0, now_utc: datetime | None = None
    ) -> bool:
        ref = now_utc if now_utc is not None else datetime.now(UTC)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=UTC)
        return (ref - self.quote_time).total_seconds() > max_age_seconds


class CandleBar(BaseModel):
    symbol: str
    timeframe: SupportedTimeframe
    candle_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def to_snapshot_payload(
        self,
        *,
        bid: Decimal,
        ask: Decimal,
        spread: Decimal,
        indicators: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candleTime": self.candle_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "open": str(self.open),
            "high": str(self.high),
            "low": str(self.low),
            "close": str(self.close),
            "volume": str(self.volume),
            "bid": str(bid),
            "ask": str(ask),
            "spread": str(spread),
            "indicators": indicators or {},
        }


def detect_server_time_offset_seconds(
    tick_epoch: int, now_utc: datetime | None = None
) -> int:
    """Detect MT5 broker server timezone offset relative to true UTC."""
    ref = now_utc if now_utc is not None else datetime.now(UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    raw_diff = tick_epoch - int(ref.timestamp())
    snapped = round(raw_diff / OFFSET_QUANTUM_SECONDS) * OFFSET_QUANTUM_SECONDS
    if abs(snapped) > MAX_REASONABLE_OFFSET_SECONDS:
        return 0
    return int(snapped)


def mt5_epoch_to_utc(mt5_epoch: int, server_offset_seconds: int) -> datetime:
    """Convert an MT5 server-shifted epoch timestamp into a true UTC datetime."""
    utc_epoch = int(mt5_epoch) - int(server_offset_seconds)
    return datetime.fromtimestamp(utc_epoch, tz=UTC)


def detect_live_server_offset(client: MT5Client, symbol: str) -> int:
    """Read a live tick for `symbol` and infer the broker server UTC offset."""
    tick = client.symbol_info_tick(symbol)
    if tick is None:
        return 0
    tick_time = int(getattr(tick, "time", 0))
    if tick_time <= 0:
        return 0
    return detect_server_time_offset_seconds(tick_time)


def fetch_live_tick(
    client: MT5Client, symbol: str, server_offset_seconds: int
) -> LiveQuote | None:
    """Read the latest live tick and normalize its timestamp to UTC."""
    tick = client.symbol_info_tick(symbol)
    if tick is None:
        return None
    tick_time = int(getattr(tick, "time", 0))
    bid = Decimal(str(getattr(tick, "bid", 0)))
    ask = Decimal(str(getattr(tick, "ask", 0)))
    if tick_time <= 0 or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return LiveQuote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        spread=ask - bid,
        quote_time=mt5_epoch_to_utc(tick_time, server_offset_seconds),
    )


def _extract_field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row[name]
    try:
        return row[name]
    except (TypeError, KeyError, IndexError):
        return getattr(row, name)


def fetch_closed_candles(
    client: MT5Client,
    symbol: str,
    timeframe: SupportedTimeframe,
    count: int,
    server_offset_seconds: int,
) -> list[CandleBar]:
    """Fetch `count` CLOSED candles starting at shift=1 (excluding forming bar 0)."""
    tf_const = TIMEFRAME_MAP[timeframe]
    rates = client.copy_rates_from_pos(symbol, tf_const, 1, count)
    if rates is None or len(rates) == 0:
        return []

    bars: list[CandleBar] = []
    for row in rates:
        ts = int(_extract_field(row, "time"))
        bars.append(
            CandleBar(
                symbol=symbol,
                timeframe=timeframe,
                candle_time=mt5_epoch_to_utc(ts, server_offset_seconds),
                open=Decimal(str(_extract_field(row, "open"))),
                high=Decimal(str(_extract_field(row, "high"))),
                low=Decimal(str(_extract_field(row, "low"))),
                close=Decimal(str(_extract_field(row, "close"))),
                volume=Decimal(str(_extract_field(row, "tick_volume"))),
            )
        )

    bars.sort(key=lambda b: b.candle_time)
    return bars
