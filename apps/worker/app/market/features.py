"""Market structure, session status, rollover break, and blackout filters."""

from __future__ import annotations

from datetime import UTC, datetime
from datetime import time as dt_time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.mt5.market_data import CandleBar

SESSION_UTC_HOURS: dict[str, tuple[int, int]] = {
    "ASIAN": (0, 8),
    "LONDON": (7, 16),
    "NEW_YORK": (12, 21),
}


class BlackoutWindowSpec(BaseModel):
    id: str = ""
    title: str = ""
    starts_at: datetime
    ends_at: datetime
    is_active: bool = True


class MarketStructure(BaseModel):
    recent_swing_high: Decimal
    recent_swing_low: Decimal
    last_candle_bullish: bool
    last_body_ratio: float


def extract_market_structure(
    bars: list[CandleBar], lookback: int = 10
) -> MarketStructure | None:
    """Compute recent swing high/low and latest closed candle body ratio."""
    if len(bars) < 2:
        return None
    ordered = sorted(bars, key=lambda b: b.candle_time)
    window = ordered[-lookback:] if len(ordered) >= lookback else ordered
    recent_high = max(b.high for b in window)
    recent_low = min(b.low for b in window)

    last = ordered[-1]
    candle_range = last.high - last.low
    body = abs(last.close - last.open)
    body_ratio = float(body / candle_range) if candle_range > 0 else 0.0

    return MarketStructure(
        recent_swing_high=recent_high,
        recent_swing_low=recent_low,
        last_candle_bullish=last.close > last.open,
        last_body_ratio=round(body_ratio, 4),
    )


def _parse_hhmm(value: str) -> dt_time:
    parts = value.strip().split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def is_in_rollover_break(
    now_utc: datetime,
    *,
    rollover_start_utc: str = "21:00",
    rollover_end_utc: str = "22:00",
) -> bool:
    """Return True if UTC timestamp falls inside the daily XAUUSD rollover break."""
    utc_dt = now_utc.astimezone(UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=UTC)
    t = utc_dt.time()
    start_t = _parse_hhmm(rollover_start_utc)
    end_t = _parse_hhmm(rollover_end_utc)
    if start_t <= end_t:
        return start_t <= t < end_t
    return t >= start_t or t < end_t


def is_in_allowed_session(
    now_utc: datetime, allowed_sessions: list[str] | None = None
) -> bool:
    """Return True if UTC hour falls inside one of the configured trading sessions."""
    if not allowed_sessions:
        return True
    utc_dt = now_utc.astimezone(UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=UTC)
    hour = utc_dt.hour
    for name in allowed_sessions:
        bounds = SESSION_UTC_HOURS.get(name.upper())
        if bounds is None:
            continue
        start_h, end_h = bounds
        if start_h <= hour < end_h:
            return True
    return False


def active_blackout_at(
    now_utc: datetime, blackouts: list[BlackoutWindowSpec]
) -> BlackoutWindowSpec | None:
    """Return the first active BlackoutWindow overlapping `now_utc`, or None."""
    utc_dt = now_utc.astimezone(UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=UTC)
    for b in blackouts:
        if not b.is_active:
            continue
        start = (
            b.starts_at.astimezone(UTC)
            if b.starts_at.tzinfo
            else b.starts_at.replace(tzinfo=UTC)
        )
        end = (
            b.ends_at.astimezone(UTC)
            if b.ends_at.tzinfo
            else b.ends_at.replace(tzinfo=UTC)
        )
        if start <= utc_dt < end:
            return b
    return None


def is_weekend_market_closed(now_utc: datetime) -> bool:
    """Approximate XAUUSD weekend closure (Fri 21:00 UTC -> Sun 22:00 UTC)."""
    utc_dt = now_utc.astimezone(UTC) if now_utc.tzinfo else now_utc.replace(tzinfo=UTC)
    wd = utc_dt.weekday()  # Mon=0 .. Sat=5, Sun=6
    if wd == 5:
        return True
    if wd == 4 and utc_dt.hour >= 21:
        return True
    if wd == 6 and utc_dt.hour < 22:
        return True
    return False


def parse_blackouts(raw_list: list[dict[str, Any]]) -> list[BlackoutWindowSpec]:
    result: list[BlackoutWindowSpec] = []
    for item in raw_list:
        result.append(
            BlackoutWindowSpec(
                id=str(item.get("id", "")),
                title=str(item.get("title", "")),
                starts_at=datetime.fromisoformat(
                    str(item["startsAt"]).replace("Z", "+00:00")
                ),
                ends_at=datetime.fromisoformat(
                    str(item["endsAt"]).replace("Z", "+00:00")
                ),
                is_active=bool(item.get("isActive", True)),
            )
        )
    return result
