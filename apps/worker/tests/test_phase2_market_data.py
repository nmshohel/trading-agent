"""Phase 2 worker tests: closed candles, quote staleness, throttling, sync tracker, Pub/Sub."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.market.candles import CandleSyncTracker, QuoteThrottler
from app.mt5.client import MT5Client
from app.mt5.market_data import (
    CandleBar,
    LiveQuote,
    fetch_closed_candles,
    fetch_live_tick,
)
from app.queue.pubsub import SseEventPublisher


class FakeMT5Market:
    def __init__(self, tick: Any = None, rates: list[dict[str, Any]] | None = None) -> None:
        self.tick = tick
        self.rates = rates or []
        self.last_copy_args: tuple[Any, ...] | None = None

    def symbol_info_tick(self, symbol: str) -> Any:
        return self.tick

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict[str, Any]]:
        self.last_copy_args = (symbol, timeframe, start_pos, count)
        return self.rates


class FakeRedisPubSub:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    def close(self) -> None:
        pass


def test_fetch_closed_candles_uses_shift_1_and_normalizes_utc() -> None:
    t1 = int(datetime(2026, 11, 23, 14, 0, 0, tzinfo=UTC).timestamp())
    t2 = int(datetime(2026, 11, 23, 14, 15, 0, tzinfo=UTC).timestamp())
    fake = FakeMT5Market(
        rates=[
            {
                "time": t2,
                "open": 2652.0,
                "high": 2656.0,
                "low": 2651.0,
                "close": 2655.5,
                "tick_volume": 320,
            },
            {
                "time": t1,
                "open": 2650.0,
                "high": 2653.0,
                "low": 2649.5,
                "close": 2652.0,
                "tick_volume": 280,
            },
        ]
    )
    client = MT5Client(mt5_module=fake)

    # Broker offset +2h (7200s)
    bars = fetch_closed_candles(client, "XAUUSDc", "M15", 2, server_offset_seconds=7200)
    assert fake.last_copy_args == ("XAUUSDc", 15, 1, 2)  # start_pos == 1 excludes live bar
    assert len(bars) == 2
    assert bars[0].candle_time == datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
    assert bars[1].candle_time == datetime(2026, 11, 23, 12, 15, 0, tzinfo=UTC)
    assert bars[1].close == Decimal("2655.5")


def test_live_tick_and_staleness_detection() -> None:
    now = datetime(2026, 11, 23, 12, 0, 30, tzinfo=UTC)
    fresh_tick = SimpleNamespace(
        time=int((now - timedelta(seconds=5)).timestamp()),
        bid=2650.10,
        ask=2650.30,
    )
    client = MT5Client(mt5_module=FakeMT5Market(tick=fresh_tick))
    quote = fetch_live_tick(client, "XAUUSDc", server_offset_seconds=0)
    assert quote is not None
    assert quote.spread == Decimal("0.2")
    assert quote.is_stale(max_age_seconds=15.0, now_utc=now) is False
    assert quote.is_stale(max_age_seconds=3.0, now_utc=now) is True


def test_quote_throttler_suppresses_duplicates_and_respects_interval() -> None:
    throttler = QuoteThrottler(min_interval_seconds=1.0, force_refresh_seconds=10.0)
    t0 = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
    q1 = LiveQuote(
        symbol="XAUUSDc",
        bid=Decimal("2650.10"),
        ask=Decimal("2650.30"),
        spread=Decimal("0.20"),
        quote_time=t0,
    )
    q2 = q1.model_copy(update={"bid": Decimal("2650.20"), "ask": Decimal("2650.40")})

    assert throttler.should_publish(q1, now_utc=t0) is True
    # Unchanged price within force_refresh window -> skip
    assert throttler.should_publish(q1, now_utc=t0 + timedelta(seconds=3)) is False
    # Changed price too fast (< min_interval) -> skip
    assert throttler.should_publish(q2, now_utc=t0 + timedelta(milliseconds=500)) is False
    # Changed price after min_interval -> publish
    assert throttler.should_publish(q2, now_utc=t0 + timedelta(seconds=1.5)) is True
    # Unchanged price after force_refresh -> publish heartbeat refresh
    assert throttler.should_publish(q2, now_utc=t0 + timedelta(seconds=12)) is True


def test_candle_sync_tracker_filters_already_synced_bars() -> None:
    tracker = CandleSyncTracker()
    b1 = CandleBar(
        symbol="XAUUSDc",
        timeframe="M15",
        candle_time=datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC),
        open=Decimal("2650"),
        high=Decimal("2652"),
        low=Decimal("2649"),
        close=Decimal("2651"),
        volume=Decimal("100"),
    )
    b2 = b1.model_copy(
        update={"candle_time": datetime(2026, 11, 23, 12, 15, 0, tzinfo=UTC)}
    )

    assert len(tracker.filter_unsynced([b1, b2])) == 2
    tracker.mark_synced(b1)
    assert tracker.filter_unsynced([b1, b2]) == [b2]
    tracker.mark_synced(b2)
    assert tracker.filter_unsynced([b1, b2]) == []


def test_sse_event_publisher_emits_valid_json_envelope() -> None:
    fake_redis = FakeRedisPubSub()
    pub = SseEventPublisher("redis://unused", channel="events:sse", redis_client=fake_redis)
    env = pub.publish_quote(
        symbol="XAUUSDc",
        bid="2650.10",
        ask="2650.30",
        spread="0.20",
        quote_time=datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC),
    )
    assert len(fake_redis.published) == 1
    channel, raw = fake_redis.published[0]
    assert channel == "events:sse"
    parsed = json.loads(raw)
    assert parsed["id"] == env.id
    assert parsed["type"] == "quote.updated"
    assert parsed["data"]["symbol"] == "XAUUSDc"
