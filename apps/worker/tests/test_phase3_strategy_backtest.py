"""Phase 3 tests: indicators, xauusd_v1 deterministic strategy, filters & backtesting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.market.features import (
    BlackoutWindowSpec,
    is_in_rollover_break,
)
from app.market.indicators import latest_indicators
from app.mt5.market_data import CandleBar
from app.strategy.signal import StrategyContext
from app.strategy.xauusd_v1 import XauusdV1Strategy
from backtest.data_loader import (
    generate_synthetic_trend_m15,
    load_candles_csv,
    resample_to_h1,
    save_candles_csv,
)
from backtest.engine import BacktestConfig, run_backtest, run_walk_forward


def _build_trend_bars(
    *,
    timeframe: str,
    count: int,
    direction: str = "UP",
    start_utc: datetime | None = None,
) -> list[CandleBar]:
    step_min = 60 if timeframe == "H1" else 15
    start = start_utc or datetime(2026, 11, 2, 8, 0, 0, tzinfo=UTC)
    sign = Decimal("1") if direction == "UP" else Decimal("-1")
    price = Decimal("2600.00")
    bars: list[CandleBar] = []
    for i in range(count):
        ts = start + timedelta(minutes=step_min * i)
        o = price
        c = price + (sign * Decimal("0.80"))
        bars.append(
            CandleBar(
                symbol="XAUUSDc",
                timeframe=timeframe,  # type: ignore[arg-type]
                candle_time=ts,
                open=o,
                high=max(o, c) + Decimal("0.60"),
                low=min(o, c) - Decimal("0.60"),
                close=c,
                volume=Decimal("200"),
            )
        )
        price = c
    return bars


def _build_setup_bars(direction: str = "UP") -> tuple[list[CandleBar], list[CandleBar]]:
    h1 = _build_trend_bars(timeframe="H1", count=60, direction=direction)
    m15 = _build_trend_bars(timeframe="M15", count=60, direction=direction)
    # Shape final two M15 closed bars into a clear pullback + continuation close
    prev = m15[-2]
    last = m15[-1]
    ind_before = latest_indicators(m15[:-1])
    assert ind_before is not None
    ema20 = Decimal(str(ind_before.ema_20))

    if direction == "UP":
        m15[-2] = prev.model_copy(
            update={
                "open": ema20 + Decimal("1.50"),
                "high": ema20 + Decimal("2.00"),
                "low": ema20 - Decimal("0.40"),
                "close": ema20 + Decimal("0.10"),
            }
        )
        m15[-1] = last.model_copy(
            update={
                "open": ema20 + Decimal("0.10"),
                "high": ema20 + Decimal("3.20"),
                "low": ema20 - Decimal("0.20"),
                "close": ema20 + Decimal("2.80"),
            }
        )
    else:
        m15[-2] = prev.model_copy(
            update={
                "open": ema20 - Decimal("1.50"),
                "high": ema20 + Decimal("0.40"),
                "low": ema20 - Decimal("2.00"),
                "close": ema20 - Decimal("0.10"),
            }
        )
        m15[-1] = last.model_copy(
            update={
                "open": ema20 - Decimal("0.10"),
                "high": ema20 + Decimal("0.20"),
                "low": ema20 - Decimal("3.20"),
                "close": ema20 - Decimal("2.80"),
            }
        )
    return h1, m15


def test_indicators_compute_deterministically() -> None:
    bars = _build_trend_bars(timeframe="M15", count=60, direction="UP")
    ind = latest_indicators(bars)
    assert ind is not None
    assert ind.ema_20 > ind.ema_50
    assert 50.0 < ind.rsi_14 <= 100.0
    assert ind.atr_14 > 0.0


def test_xauusd_v1_produces_reproducible_long_and_short_candidates() -> None:
    strat = XauusdV1Strategy()
    # Tuesday 13:00 UTC (London + NY overlap)
    now = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)

    h1_up, m15_up = _build_setup_bars("UP")
    bid = m15_up[-1].close
    ctx_long = StrategyContext(
        symbol="XAUUSDc",
        h1_bars=h1_up,
        m15_bars=m15_up,
        current_bid=bid,
        current_ask=bid + Decimal("0.20"),
        max_spread=Decimal("0.50"),
        now_utc=now,
    )
    cand_long = strat.evaluate(ctx_long)
    assert cand_long is not None
    assert cand_long.direction == "BUY"
    assert cand_long.stop_loss < cand_long.entry_reference < cand_long.take_profit
    assert cand_long.order_type == "MARKET"

    # Re-evaluating with same dedup_key must return None
    ctx_dup = ctx_long.model_copy(update={"existing_dedup_keys": {cand_long.dedup_key}})
    assert strat.evaluate(ctx_dup) is None

    # Mirror short candidate
    h1_dn, m15_dn = _build_setup_bars("DOWN")
    bid_dn = m15_dn[-1].close
    ctx_short = StrategyContext(
        symbol="XAUUSDc",
        h1_bars=h1_dn,
        m15_bars=m15_dn,
        current_bid=bid_dn,
        current_ask=bid_dn + Decimal("0.20"),
        max_spread=Decimal("0.50"),
        now_utc=now,
    )
    cand_short = strat.evaluate(ctx_short)
    assert cand_short is not None
    assert cand_short.direction == "SELL"
    assert cand_short.take_profit < cand_short.entry_reference < cand_short.stop_loss


def test_strategy_filters_block_rollover_blackout_cooldown_and_spread() -> None:
    strat = XauusdV1Strategy()
    h1, m15 = _build_setup_bars("UP")
    bid = m15[-1].close
    now = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)

    base = StrategyContext(
        symbol="XAUUSDc",
        h1_bars=h1,
        m15_bars=m15,
        current_bid=bid,
        current_ask=bid + Decimal("0.20"),
        max_spread=Decimal("0.50"),
        now_utc=now,
    )
    assert strat.evaluate(base) is not None

    # High spread blocked
    assert strat.evaluate(base.model_copy(update={"current_ask": bid + Decimal("0.90")})) is None

    # Rollover break blocked (21:30 UTC)
    rollover_dt = datetime(2026, 11, 3, 21, 30, 0, tzinfo=UTC)
    assert is_in_rollover_break(rollover_dt) is True
    assert strat.evaluate(base.model_copy(update={"now_utc": rollover_dt})) is None

    # Active blackout window blocked
    blackout = BlackoutWindowSpec(
        id="bo_1",
        title="US CPI",
        starts_at=now - timedelta(minutes=10),
        ends_at=now + timedelta(minutes=20),
        is_active=True,
    )
    assert strat.evaluate(base.model_copy(update={"blackouts": [blackout]})) is None

    # Active post-rejection cooldown blocked
    cooldown_ctx = base.model_copy(
        update={"cooldown_until": now + timedelta(minutes=10)}
    )
    assert strat.evaluate(cooldown_ctx) is None


def test_backtest_engine_csv_roundtrip_and_walk_forward(tmp_path: Path) -> None:
    bars = generate_synthetic_trend_m15(num_bars=420, direction="UP")
    csv_path = tmp_path / "xauusdc_m15.csv"
    save_candles_csv(bars, csv_path)
    loaded = load_candles_csv(csv_path)
    assert len(loaded) == len(bars)
    assert len(resample_to_h1(loaded)) >= 100

    strat = XauusdV1Strategy(ema_fast=10, ema_mid=20, ema_slow=50)
    cfg = BacktestConfig(
        initial_equity=Decimal("10000.00"),
        risk_percent=Decimal("0.005"),
        commission_per_lot=Decimal("0.07"),
        daily_swap_per_lot=Decimal("-0.50"),
    )
    metrics, trades = run_backtest(loaded, strategy=strat, config=cfg)
    assert metrics.initial_equity == Decimal("10000.00")
    assert metrics.total_trades >= 1
    assert len(trades) == metrics.total_trades

    wf = run_walk_forward(loaded, strategy=strat, config=cfg)
    assert "in_sample" in wf and "out_of_sample" in wf
