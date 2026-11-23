"""Historical candle CSV loader, saver, and synthetic fixture builder for backtesting."""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.mt5.market_data import CandleBar, SupportedTimeframe


def save_candles_csv(bars: list[CandleBar], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(bars, key=lambda b: b.candle_time)
    with target.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["symbol", "timeframe", "candle_time", "open", "high", "low", "close", "volume"]
        )
        for b in ordered:
            writer.writerow(
                [
                    b.symbol,
                    b.timeframe,
                    b.candle_time.astimezone(UTC).isoformat(),
                    str(b.open),
                    str(b.high),
                    str(b.low),
                    str(b.close),
                    str(b.volume),
                ]
            )


def load_candles_csv(path: str | Path) -> list[CandleBar]:
    bars: list[CandleBar] = []
    with Path(path).open("r", newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            bars.append(
                CandleBar(
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],  # type: ignore[arg-type]
                    candle_time=datetime.fromisoformat(
                        row["candle_time"].replace("Z", "+00:00")
                    ).astimezone(UTC),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=Decimal(row["volume"]),
                )
            )
    bars.sort(key=lambda b: b.candle_time)
    return bars


def resample_to_h1(m15_bars: list[CandleBar]) -> list[CandleBar]:
    """Aggregate chronological M15 closed bars into completed H1 bars."""
    if not m15_bars:
        return []
    ordered = sorted(m15_bars, key=lambda b: b.candle_time)
    buckets: dict[datetime, list[CandleBar]] = {}
    for bar in ordered:
        hour_start = bar.candle_time.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(hour_start, []).append(bar)

    h1_bars: list[CandleBar] = []
    for hour_start in sorted(buckets.keys()):
        group = buckets[hour_start]
        if len(group) < 4:
            continue
        h1_bars.append(
            CandleBar(
                symbol=group[0].symbol,
                timeframe="H1",
                candle_time=hour_start,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum((b.volume for b in group), Decimal("0")),
            )
        )
    return h1_bars


def generate_synthetic_trend_m15(
    *,
    symbol: str = "XAUUSDc",
    num_bars: int = 320,
    start_utc: datetime | None = None,
    direction: str = "UP",
) -> list[CandleBar]:
    """Generate deterministic trending M15 XAUUSD fixture candles with periodic pullbacks."""
    start = start_utc or datetime(2026, 11, 2, 0, 0, 0, tzinfo=UTC)  # Monday UTC
    sign = Decimal("1") if direction.upper() == "UP" else Decimal("-1")
    price = Decimal("2600.00")
    bars: list[CandleBar] = []

    for i in range(num_bars):
        ts = start + timedelta(minutes=15 * i)
        # Every 24 bars create a clean 2-bar pullback into EMA20 then continuation breakout
        cycle = i % 24
        if cycle == 20:
            delta = -sign * Decimal("3.20")
        elif cycle == 21:
            delta = -sign * Decimal("1.80")
        elif cycle == 22:
            delta = sign * Decimal("4.50")
        else:
            delta = sign * Decimal("0.55")

        o = price
        c = price + delta
        hi = max(o, c) + Decimal("0.90")
        lo = min(o, c) - Decimal("0.90")
        bars.append(
            CandleBar(
                symbol=symbol,
                timeframe="M15",
                candle_time=ts,
                open=o.quantize(Decimal("0.01")),
                high=hi.quantize(Decimal("0.01")),
                low=lo.quantize(Decimal("0.01")),
                close=c.quantize(Decimal("0.01")),
                volume=Decimal("250"),
            )
        )
        price = c

    return bars


def timeframe_minutes(tf: SupportedTimeframe) -> int:
    return {"M5": 5, "M15": 15, "H1": 60}[tf]
