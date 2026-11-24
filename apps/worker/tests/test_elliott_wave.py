"""Unit tests for causal Elliott Wave detector & XauusdElliottWaveV1Strategy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.market.elliott_wave import (
    SwingPivot,
    count_macd_divergences,
    detect_abc_reversal,
    detect_causal_zigzag,
    detect_wave_3,
    detect_wave_5,
)
from app.market.indicators import EwIndicatorSeries
from app.market.smc import (
    detect_crt,
    detect_fvg_zones,
    detect_hammer_or_pinbar,
    detect_order_blocks,
    detect_significant_swings_idm,
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

    # M15 bars: prior pullback to Anchor P0=3600 (negative MACD) -> Wave 1 -> 50% Wave 2 -> trigger
    m15_prices: list[tuple[float, float, float, float]] = []
    # 45 warmup bars around 3620, then 15 bars falling to 3600 so Anchor P0 has deep negative MACD
    for i in range(45):
        base = 3622.0 + (i % 3) * 0.4
        m15_prices.append((base, base + 1.0, base - 1.0, base + 0.1))
    for i in range(15):
        base = 3622.0 - i * 1.5
        m15_prices.append((base, base + 0.8, base - 1.2, base - 1.0))
    # Wave 1: 3600 -> 3630 (+30, MACD > 0), Wave 2: 3615 (50% retrace, MACD > Anchor)
    wave_seq = [
        (3600.5, 3609.0, 3600.0, 3608.5),
        (3608.5, 3617.0, 3608.0, 3616.5),
        (3616.5, 3624.0, 3616.0, 3623.5),
        (3623.5, 3630.0, 3623.0, 3629.0),  # Wave 1 peak 3630
        (3629.0, 3629.2, 3623.0, 3623.5),
        (3623.5, 3624.0, 3618.0, 3618.5),
        (3618.5, 3619.0, 3615.0, 3616.0),  # Wave 2 low 3615 (50% retrace)
        (3616.0, 3625.0, 3615.8, 3624.5),  # Strong bullish MSS + displacement candle!
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
    assert "smcConfluence" in cand.indicators


def _mock_series(macd_vals: list[float]) -> EwIndicatorSeries:
    n = len(macd_vals)
    return EwIndicatorSeries(
        ema_21=[105.0] * n,
        ema_144=[100.0] * n,
        macd_line=macd_vals,
        signal_line=[0.0] * n,
        macd_hist=[0.0] * n,
        rsi_14=[55.0] * n,
        atr_14=[2.0] * n,
    )


def test_wave_2_hard_812_close_invalidation_vs_wick_tolerance() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    # Wave 1: 100 -> 110 (81.2% retrace limit = 101.88)
    pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=4, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=7, candle_time=t0, price=Decimal("101.5"), kind="LOW"),
    ]
    # Wick dips to 101.5 (< 101.88) but body CLOSES at 103.0 (> 101.88) -> VALID Wave 2!
    wick_only_bars = _make_bars(
        [(105.0, 106.0, 104.0, 105.0)] * 7
        + [(104.0, 104.5, 101.5, 103.0)]
        + [(103.0, 106.0, 102.8, 105.5)] * 3
    )
    assert (
        detect_wave_3(wick_only_bars, pivots, direction="BUY", atr_value=Decimal("2.0"))
        is not None
    )

    # Single candle CLOSES at 101.6 (< 101.88) -> MUST REJECT (Wave B, NOT Wave 2!)
    close_broken_bars = _make_bars(
        [(105.0, 106.0, 104.0, 105.0)] * 7
        + [(104.0, 104.5, 101.5, 101.6)]
        + [(101.6, 106.0, 101.5, 105.5)] * 3
    )
    assert (
        detect_wave_3(
            close_broken_bars, pivots, direction="BUY", atr_value=Decimal("2.0")
        )
        is None
    )


def test_macd_hard_rules_w2_base_w3_peak_and_ac_divergence() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    bars = _make_bars([(105.0, 107.0, 104.5, 106.5)] * 16)

    # 1. Wave 2 MACD breaks Anchor Base Point (-3.0 < -2.0) -> REJECT (Wave B)
    w2_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
    ]
    bad_w2_macd = [0.0] * 16
    bad_w2_macd[1] = -2.0
    bad_w2_macd[5] = 2.0
    bad_w2_macd[9] = -3.0  # Breaks Base Point!
    assert (
        detect_wave_3(
            bars,
            w2_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(bad_w2_macd),
        )
        is None
    )

    # 2. Wave 3 MACD peak <= Wave 1 MACD peak (1.5 <= 2.5) -> REJECT (Wave C)
    w5_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=4, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=7, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
        SwingPivot(index=10, candle_time=t0, price=Decimal("124.0"), kind="HIGH"),
        SwingPivot(index=13, candle_time=t0, price=Decimal("117.0"), kind="LOW"),
    ]
    w5_bars = _make_bars([(118.0, 121.0, 117.5, 120.5)] * 16)
    bad_w3_macd = [0.0] * 16
    bad_w3_macd[4] = 2.5
    bad_w3_macd[10] = 1.5  # W3 MACD lower than W1 -> NOT Wave 3!
    assert (
        detect_wave_5(
            w5_bars,
            w5_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(bad_w3_macd),
        )
        is None
    )

    # 3. Wave C MACD breaks below Wave A MACD (-4.0 < -2.0) -> REJECT (no positive A-C div)
    abc_pivots = [
        SwingPivot(index=2, candle_time=t0, price=Decimal("120.0"), kind="HIGH"),
        SwingPivot(index=6, candle_time=t0, price=Decimal("110.0"), kind="LOW"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("115.0"), kind="HIGH"),
        SwingPivot(index=13, candle_time=t0, price=Decimal("105.0"), kind="LOW"),
    ]
    bad_ac_macd = [0.0] * 16
    bad_ac_macd[6] = -2.0
    bad_ac_macd[13] = -4.0  # Lower MACD on C -> breaks Wave A MACD!
    assert (
        detect_abc_reversal(
            bars,
            abc_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(bad_ac_macd),
        )
        is None
    )

    # 4. Anchor Point counts 2 consecutive Positive MACD Divergences & returns latest
    div_pivots = [
        SwingPivot(index=2, candle_time=t0, price=Decimal("110.0"), kind="LOW"),
        SwingPivot(index=6, candle_time=t0, price=Decimal("105.0"), kind="LOW"),
        SwingPivot(index=10, candle_time=t0, price=Decimal("101.0"), kind="LOW"),
    ]
    div_macd = [0.0] * 16
    div_macd[2] = -5.0
    div_macd[6] = -3.0
    div_macd[10] = -1.2
    cnt, latest = count_macd_divergences(div_pivots, div_macd, direction="BUY")
    assert cnt == 2
    assert latest is not None and latest.index == 10


def test_smc_detectors_crt_hammer_fvg_ob_and_idm_swings() -> None:
    # CRT: bar 1 sweeps below bar 0 low (99 < 100) and closes inside & bullish (102)
    crt_bars = _make_bars(
        [
            (101.0, 105.0, 100.0, 104.0),
            (103.0, 103.5, 99.0, 102.0),
            (102.0, 106.0, 101.8, 105.5),
        ]
    )
    assert detect_crt(crt_bars, "BUY") is True

    # Hammer: long lower wick, small body near top
    hammer = _make_bars([(104.0, 105.0, 99.0, 104.8)])[0]
    assert detect_hammer_or_pinbar(hammer, "BUY") is True

    # Bullish FVG + OB
    fvg_bars = _make_bars(
        [
            (104.0, 104.5, 100.0, 101.0),  # Bearish OB
            (101.0, 108.0, 100.8, 107.5),  # Strong bullish displacement
            (107.5, 111.0, 106.5, 110.5),  # Leaves gap 104.5 -> 106.5
            (110.5, 112.0, 109.0, 111.5),
        ]
    )
    assert len(detect_fvg_zones(fvg_bars, "BUY")) >= 1
    assert len(detect_order_blocks(fvg_bars, "BUY", Decimal("2.0"))) >= 1

    # Significant swings with IDM sweep -> BOS
    idm_bars = _make_bars(
        [
            (100, 102, 99, 101),
            (101, 106, 100.5, 105),
            (105, 108, 104, 107),
            (107, 107.5, 102.5, 103.5),  # 3-bar fractal internal pullback low = 102.5 (IDM)
            (103.5, 110, 103.0, 109),    # Major high candidate = 110
            (109, 109.5, 106, 107),
            (107, 107.5, 101.5, 104),    # Sweeps IDM (101.5 < 102.5)!
            (104, 108, 103.5, 107.5),
            (107.5, 112, 107, 111.5),    # BOS above 110!
            (111.5, 113, 111, 112.5),
        ]
    )
    swings = detect_significant_swings_idm(idm_bars)
    assert any(s.swept_idm for s in swings)
    assert any(s.confirmed_bos for s in swings)

