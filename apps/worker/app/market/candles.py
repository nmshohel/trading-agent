"""Quote throttling and closed-candle incremental sync trackers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.mt5.market_data import CandleBar, LiveQuote


class QuoteThrottler:
    """Suppresses unchanged or excessively frequent quote publications."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = 1.0,
        force_refresh_seconds: float = 10.0,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._force_refresh = force_refresh_seconds
        self._last_bid: Decimal | None = None
        self._last_ask: Decimal | None = None
        self._last_published_at: datetime | None = None

    def should_publish(
        self, quote: LiveQuote, now_utc: datetime | None = None
    ) -> bool:
        now = now_utc if now_utc is not None else datetime.now(UTC)
        if self._last_published_at is None:
            self._record(quote, now)
            return True

        elapsed = (now - self._last_published_at).total_seconds()
        changed = quote.bid != self._last_bid or quote.ask != self._last_ask

        if changed and elapsed >= self._min_interval:
            self._record(quote, now)
            return True

        if elapsed >= self._force_refresh:
            self._record(quote, now)
            return True

        return False

    def _record(self, quote: LiveQuote, now: datetime) -> None:
        self._last_bid = quote.bid
        self._last_ask = quote.ask
        self._last_published_at = now


class CandleSyncTracker:
    """Tracks the newest synced closed candle timestamp per (symbol, timeframe)."""

    def __init__(self) -> None:
        self._last_synced: dict[tuple[str, str], datetime] = {}

    def filter_unsynced(self, bars: list[CandleBar]) -> list[CandleBar]:
        if not bars:
            return []
        key = (bars[0].symbol, bars[0].timeframe)
        watermark = self._last_synced.get(key)
        ordered = sorted(bars, key=lambda b: b.candle_time)
        if watermark is None:
            return ordered
        return [b for b in ordered if b.candle_time > watermark]

    def mark_synced(self, bar: CandleBar) -> None:
        key = (bar.symbol, bar.timeframe)
        prev = self._last_synced.get(key)
        if prev is None or bar.candle_time > prev:
            self._last_synced[key] = bar.candle_time
