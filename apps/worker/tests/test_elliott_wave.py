"""Unit tests for causal Elliott Wave detector & XauusdElliottWaveV1Strategy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.elliott_wave import (
    SwingPivot,
    detect_abc_reversal,
    detect_causal_zigzag,
    detect_wave_3,
    detect_wave_5,
)
from app.mt5.market_data import CandleBar
from app.strategy.signal import StrategyContext
from app.strategy.xauusd_ew_v1 import XauusdElliottWaveV1Strategy


def _make_bars(prices: list[tuple[float, float, float, float]]) -> list[CandleBar]:
    start = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    out: list[CandleBar] = []
    for idx, (op, hi, lo, cl) in enumerate(prices):
        out.append(
            CandleBar(
                symbol="XAUUSDm",
                timeframe="M15",
                candle_time=start + timedelta(minutes=15 * idx),
                open=Decimal(f"{op:.3f}"),
                high=Decimal(f"{hi:.3f}"),
                low=Decimal(f"{lo:.3f}"),
                close=Decimal(f"{cl:.3f}"),
                volume=Decimal("100"),
            )
        )
    return out


def test_causal_zigzag_has_zero_lookahead() -> None:
    # Build clear up -> down -> up -> down wave path
    closes = [
        100, 102, 105, 108, 110,  # rally to 110
        107, 104, 102, 103,       # pullback to 102
        106, 111, 115, 118,       # rally to 118
        114, 111, 109,            # pullback to 109
        113, 117, 121,            # rally to 121
    ]
    bars = _make_bars([(p - 0.5, p + 0.5, p - 0.5, p) for p in closes])
    prefix_pivots = detect_causal_zigzag(
        bars[:13], atr_value=Decimal("2.0"), reversal_atr_mult=Decimal("1.5")
    )
    full_pivots = detect_causal_zigzag(
        bars, atr_value=Decimal("2.0"), reversal_atr_mult=Decimal("1.5")
    )
    assert len(prefix_pivots) >= 2
    # Every pivot confirmed on prefix must remain identical on full history (zero repaint)
    assert full_pivots[: len(prefix_pivots)] == prefix_pivots


def test_wave_3_enforces_cardinal_rule_1_and_fib_retrace() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    bars = _make_bars([(104.0, 106.0, 103.5, 105.5)] * 12)
    # Valid Bullish Wave 1 (100 -> 110), Wave 2 pullback to 104 (60% Fib retrace)
    valid_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
    ]
    pat = detect_wave_3(bars, valid_pivots, direction="BUY", atr_value=Decimal("2.0"))
    assert pat is not None
    assert pat.setup_type == "WAVE_3"
    assert pat.direction == "BUY"
    assert 0.59 <= pat.retracement_ratio <= 0.61

    # Invalid: Wave 2 breaks Wave 1 origin (99.0 < 100.0) -> MUST reject!
    broken_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("99.0"), kind="LOW"),
    ]
    assert (
        detect_wave_3(bars, broken_pivots, direction="BUY", atr_value=Decimal("2.0"))
        is None
    )


def test_wave_5_enforces_no_overlap_cardinal_rule_3() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    bars = _make_bars([(118.0, 121.0, 117.5, 120.5)] * 16)
    # Valid: P0=100, P1=110 (W1=10), P2=104, P3=124 (W3=20=2xW1), P4=117 (35% of W3, > P1=110)
    valid_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=4, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=7, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
        SwingPivot(index=10, candle_time=t0, price=Decimal("124.0"), kind="HIGH"),
        SwingPivot(index=13, candle_time=t0, price=Decimal("117.0"), kind="LOW"),
    ]
    pat = detect_wave_5(bars, valid_pivots, direction="BUY", atr_value=Decimal("2.0"))
    assert pat is not None
    assert pat.setup_type == "WAVE_5"
    assert pat.extension_ratio == 2.0

    # Invalid: Wave 4 overlaps Wave 1 peak (P4=109.5 <= P1=110.0) -> MUST reject!
    overlap_pivots = list(valid_pivots[:-1]) + [
        SwingPivot(index=13, candle_time=t0, price=Decimal("109.5"), kind="LOW")
    ]
    assert (
        detect_wave_5(bars, overlap_pivots, direction="BUY", atr_value=Decimal("2.0"))
        is None
    )


def test_abc_reversal_detects_measured_c_wave_completion() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    bars = _make_bars([(108.0, 111.0, 107.5, 110.5)] * 16)
    # Downward A-B-C: A: 120->110 (10), B: 110->115 (50%), C: 115->105 (1.0x A)
    abc_pivots = [
        SwingPivot(index=2, candle_time=t0, price=Decimal("120.0"), kind="HIGH"),
        SwingPivot(index=6, candle_time=t0, price=Decimal("110.0"), kind="LOW"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("115.0"), kind="HIGH"),
        SwingPivot(index=13, candle_time=t0, price=Decimal("105.0"), kind="LOW"),
    ]
    pat = detect_abc_reversal(
        bars, abc_pivots, direction="BUY", atr_value=Decimal("2.0")
    )
    assert pat is not None
    assert pat.setup_type == "ABC_REVERSAL"
    assert pat.direction == "BUY"
    assert pat.extension_ratio == 1.0


def test_elliott_wave_strategy_generates_valid_buy_candidate() -> None:
    # Build H1 uptrend bars (65 bars steadily rising)
    h1_start = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)
    h1_bars = [
        CandleBar(
            symbol="XAUUSDm",
            timeframe="H1",
            candle_time=h1_start + timedelta(hours=i),
            open=Decimal(f"{3500 + i * 2.0:.3f}"),
            high=Decimal(f"{3502 + i * 2.0:.3f}"),
            low=Decimal(f"{3499 + i * 2.0:.3f}"),
            close=Decimal(f"{3501.5 + i * 2.0:.3f}"),
            volume=Decimal("500"),
        )
        for i in range(65)
    ]

    # M15 bars: warmup + pullback to 3600 (P0) + Wave 1 + 50% Wave 2 + bullish trigger
    m15_prices: list[tuple[float, float, float, float]] = []
    # 56 warmup bars rising gently 3580 -> 3608, then a clear pullback to 3600 to establish P0
    for i in range(56):
        base = 3580.0 + i * 0.5
        m15_prices.append((base, base + 1.0, base - 1.0, base + 0.4))
    # Pullback from 3616 down to 3600 so ZigZag confirms HIGH (~3616) then LOW (3600 = P0)
    m15_prices.extend([
        (3608.0, 3616.0, 3607.5, 3615.0),
        (3615.0, 3615.5, 3607.0, 3608.0),
        (3608.0, 3608.5, 3600.0, 3601.0),
        (3601.0, 3604.0, 3600.2, 3603.5),
    ])
    # Wave 1 up from 3600 -> 3628 (+28), Wave 2 down to 3614 (50% retrace), then bullish bounce
    wave_seq = [
        (3603.5, 3612.0, 3603.0, 3611.5),
        (3611.5, 3620.0, 3611.0, 3619.5),
        (3619.5, 3628.0, 3619.0, 3627.0),  # Wave 1 peak 3628
        (3627.0, 3627.2, 3621.0, 3621.5),
        (3621.5, 3622.0, 3617.0, 3617.5),
        (3617.5, 3618.0, 3614.0, 3615.0),  # Wave 2 low 3614 (50% retrace)
        (3615.0, 3622.8, 3614.8, 3622.2),  # Strong bullish reversal candle!
    ]
    m15_prices.extend(wave_seq)
    m15_bars = _make_bars(m15_prices)

    strat = XauusdElliottWaveV1Strategy(price_digits=3)
    ctx = StrategyContext(
        symbol="XAUUSDm",
        now_utc=datetime(2026, 11, 3, 13, 30, tzinfo=UTC),  # London/NY overlap
        current_bid=Decimal("3622.200"),
        current_ask=Decimal("3622.440"),
        current_spread=Decimal("0.240"),
        max_spread=Decimal("0.600"),
        h1_bars=h1_bars,
        m15_bars=m15_bars,
    )
    cand = strat.evaluate(ctx)
    assert cand is not None
    assert cand.direction == "BUY"
    assert cand.stop_loss < cand.entry_reference < cand.take_profit
    assert cand.reward_risk >= Decimal("2.00")
    assert "elliottWave" in cand.indicators
