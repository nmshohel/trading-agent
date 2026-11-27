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

    # M15 bars: 2x Positive MACD Divergence into Anchor P0=3598 -> Wave 1 -> 50% Wave 2 -> trigger
    m15_prices: list[tuple[float, float, float, float]] = []
    # 40 warmup bars around 3628 (so total >= 60 M15 bars)
    for i in range(40):
        base = 3628.0 + (i % 3) * 0.3
        m15_prices.append((base, base + 0.8, base - 0.8, base + 0.1))
    # Sharp plunge to Low #1 = 3604 (deepest MACD trough)
    plunge_1 = [
        (3628.0, 3628.5, 3620.0, 3621.0),
        (3621.0, 3621.5, 3612.0, 3613.0),
        (3613.0, 3613.5, 3604.0, 3605.0),
        # Bounce to 3618 (lifts MACD)
        (3605.0, 3612.0, 3604.8, 3611.5),
        (3611.5, 3618.0, 3611.0, 3617.0),
        (3617.0, 3618.5, 3614.0, 3615.0),
        # Slower drop to Low #2 = 3601 (1st Positive Divergence: LL price, HL MACD)
        (3615.0, 3615.2, 3608.0, 3608.5),
        (3608.5, 3609.0, 3603.0, 3604.0),
        (3604.0, 3604.5, 3601.0, 3602.5),
        # Bounce to 3615 (lifts MACD further)
        (3602.5, 3609.0, 3602.0, 3608.5),
        (3608.5, 3615.0, 3608.0, 3614.0),
        (3614.0, 3615.5, 3611.0, 3612.0),
        # Final weak drop to Anchor P0 = 3598 (2nd Positive Divergence: LL price, HL MACD!)
        (3612.0, 3612.2, 3605.0, 3606.0),
        (3606.0, 3606.5, 3600.5, 3601.5),
        (3601.5, 3602.0, 3598.0, 3600.0),
    ]
    m15_prices.extend(plunge_1)
    # Wave 1: 3598 -> 3630 (+32, MACD > 0), Wave 2: 3614 (50% retrace, MACD > Anchor)
    wave_seq = [
        (3600.0, 3609.0, 3599.5, 3608.5),
        (3608.5, 3617.0, 3608.0, 3616.5),
        (3616.5, 3624.0, 3616.0, 3623.5),
        (3623.5, 3630.0, 3623.0, 3629.0),  # Wave 1 peak 3630
        (3629.0, 3629.2, 3623.0, 3623.5),
        (3623.5, 3624.0, 3618.0, 3618.5),
        (3618.5, 3619.0, 3614.0, 3615.5),  # Wave 2 low 3614 (50% retrace)
        (3615.5, 3625.0, 3615.2, 3624.5),  # Strong bullish MSS + displacement candle!
    ]
    m15_prices.extend(wave_seq)
    m15_bars = _make_bars(m15_prices)

    strat = XauusdElliottWaveV1Strategy(price_digits=3)
    ctx = StrategyContext(
        symbol="XAUUSDm",
        now_utc=datetime(2026, 11, 3, 13, 30, tzinfo=UTC),  # London/NY overlap
        current_bid=Decimal("3624.200"),
        current_ask=Decimal("3624.440"),
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
    assert cand.indicators["elliottWave"]["anchorDivergences"] >= 2
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


def test_mandatory_two_plus_macd_divergence_gate() -> None:
    t0 = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    # 1. Wave 3 BUY: only 1 divergence -> MUST REJECT; 2 divergences -> ACCEPT
    bars_w3 = _make_bars([(105.0, 107.0, 104.5, 106.5)] * 20)
    one_div_pivots = [
        SwingPivot(index=2, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("108.0"), kind="HIGH"),
        SwingPivot(index=8, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=12, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=16, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
    ]
    one_div_macd = [0.0] * 20
    one_div_macd[2] = -5.0
    one_div_macd[8] = -2.0  # Only 1 divergence (idx 2 -> 8)
    one_div_macd[12] = 2.5
    one_div_macd[16] = -1.0
    assert (
        detect_wave_3(
            bars_w3,
            one_div_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(one_div_macd),
            min_anchor_divergences=2,
        )
        is None
    )

    two_div_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("106.0"), kind="LOW"),
        SwingPivot(index=3, candle_time=t0, price=Decimal("109.0"), kind="HIGH"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("103.0"), kind="LOW"),
        SwingPivot(index=7, candle_time=t0, price=Decimal("107.0"), kind="HIGH"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=13, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=16, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
    ]
    two_div_macd = [0.0] * 20
    two_div_macd[1] = -6.0
    two_div_macd[5] = -3.5  # 1st divergence
    two_div_macd[9] = -1.5  # 2nd divergence
    two_div_macd[13] = 2.5
    two_div_macd[16] = -0.8  # Above Anchor Base (-1.5)
    pat_w3 = detect_wave_3(
        bars_w3,
        two_div_pivots,
        direction="BUY",
        atr_value=Decimal("2.0"),
        series=_mock_series(two_div_macd),
        min_anchor_divergences=2,
    )
    assert pat_w3 is not None
    assert pat_w3.anchor_divergences >= 2

    # 2. Wave 5 BUY: 0/1 divergence at Anchor P0 -> MUST REJECT; 2+ -> ACCEPT
    bars_w5 = _make_bars([(118.0, 121.0, 117.5, 120.5)] * 26)
    w5_no_div_pivots = [
        SwingPivot(index=9, candle_time=t0, price=Decimal("100.0"), kind="LOW"),
        SwingPivot(index=12, candle_time=t0, price=Decimal("110.0"), kind="HIGH"),
        SwingPivot(index=15, candle_time=t0, price=Decimal("104.0"), kind="LOW"),
        SwingPivot(index=19, candle_time=t0, price=Decimal("124.0"), kind="HIGH"),
        SwingPivot(index=22, candle_time=t0, price=Decimal("117.0"), kind="LOW"),
    ]
    w5_macd = [0.0] * 26
    w5_macd[1] = -6.0
    w5_macd[5] = -3.5
    w5_macd[9] = -1.5
    w5_macd[12] = 2.0
    w5_macd[15] = 0.2
    w5_macd[19] = 4.5  # W3 > W1
    w5_macd[22] = -0.2  # W4 < W2 reset
    assert (
        detect_wave_5(
            bars_w5,
            w5_no_div_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(w5_macd),
            min_anchor_divergences=2,
        )
        is None
    )
    w5_two_div_pivots = [
        SwingPivot(index=1, candle_time=t0, price=Decimal("106.0"), kind="LOW"),
        SwingPivot(index=3, candle_time=t0, price=Decimal("109.0"), kind="HIGH"),
        SwingPivot(index=5, candle_time=t0, price=Decimal("103.0"), kind="LOW"),
        SwingPivot(index=7, candle_time=t0, price=Decimal("107.0"), kind="HIGH"),
    ] + w5_no_div_pivots
    pat_w5 = detect_wave_5(
        bars_w5,
        w5_two_div_pivots,
        direction="BUY",
        atr_value=Decimal("2.0"),
        series=_mock_series(w5_macd),
        min_anchor_divergences=2,
    )
    assert pat_w5 is not None
    assert pat_w5.anchor_divergences >= 2

    # 3. ABC Reversal BUY: 1 divergence (A-C only) -> REJECT; A-C + internal W-C -> ACCEPT
    abc_bars = _make_bars([(108.0, 111.0, 107.5, 110.5)] * 20)
    abc_pivots = [
        SwingPivot(index=2, candle_time=t0, price=Decimal("120.0"), kind="HIGH"),
        SwingPivot(index=6, candle_time=t0, price=Decimal("110.0"), kind="LOW"),
        SwingPivot(index=9, candle_time=t0, price=Decimal("115.0"), kind="HIGH"),
        SwingPivot(index=16, candle_time=t0, price=Decimal("105.0"), kind="LOW"),
    ]
    abc_one_div_macd = [0.0] * 20
    abc_one_div_macd[6] = -4.0
    abc_one_div_macd[16] = -2.0  # Only Major A-C (1 divergence)
    assert (
        detect_abc_reversal(
            abc_bars,
            abc_pivots,
            direction="BUY",
            atr_value=Decimal("2.0"),
            series=_mock_series(abc_one_div_macd),
            min_anchor_divergences=2,
        )
        is None
    )

    # Add internal Wave-C W3 -> W5 divergence (bar 12 low 106.5 -> bar 16 low 105.0, MACD rises)
    abc_two_div_prices = [(108.0, 111.0, 107.5, 110.5)] * 20
    abc_two_div_prices[12] = (108.0, 108.5, 106.5, 107.5)
    abc_two_div_prices[16] = (107.0, 107.5, 105.0, 106.8)
    abc_two_div_bars = _make_bars(abc_two_div_prices)
    abc_two_div_macd = [0.0] * 20
    abc_two_div_macd[6] = -5.0
    abc_two_div_macd[12] = -3.0
    abc_two_div_macd[16] = -1.2  # Higher than both W-A (-5.0) and internal W-3 (-3.0)!
    pat_abc = detect_abc_reversal(
        abc_two_div_bars,
        abc_pivots,
        direction="BUY",
        atr_value=Decimal("2.0"),
        series=_mock_series(abc_two_div_macd),
        min_anchor_divergences=2,
    )
    assert pat_abc is not None
    assert pat_abc.anchor_divergences >= 2


def test_mtf_h1_bias_m15_counting_m5_trigger() -> None:
    """Verify full 3-TF pipeline: H1 (HTF) -> M15 (Elliott Counting) -> M5 (LTF Trigger)."""
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

    # M15: 2x Positive MACD Divergence into Anchor P0=3598 -> W1=3630 -> W2=3614
    m15_prices: list[tuple[float, float, float, float]] = []
    for i in range(40):
        base = 3628.0 + (i % 3) * 0.3
        m15_prices.append((base, base + 0.8, base - 0.8, base + 0.1))
    m15_prices.extend(
        [
            (3628.0, 3628.5, 3620.0, 3621.0),
            (3621.0, 3621.5, 3612.0, 3613.0),
            (3613.0, 3613.5, 3604.0, 3605.0),
            (3605.0, 3612.0, 3604.8, 3611.5),
            (3611.5, 3618.0, 3611.0, 3617.0),
            (3617.0, 3618.5, 3614.0, 3615.0),
            (3615.0, 3615.2, 3608.0, 3608.5),
            (3608.5, 3609.0, 3603.0, 3604.0),
            (3604.0, 3604.5, 3601.0, 3602.5),
            (3602.5, 3609.0, 3602.0, 3608.5),
            (3608.5, 3615.0, 3608.0, 3614.0),
            (3614.0, 3615.5, 3611.0, 3612.0),
            (3612.0, 3612.2, 3605.0, 3606.0),
            (3606.0, 3606.5, 3600.5, 3601.5),
            (3601.5, 3602.0, 3598.0, 3600.0),
            # Wave 1 & Wave 2 pullback on M15 (note: last M15 is still pulling back!)
            (3600.0, 3609.0, 3599.5, 3608.5),
            (3608.5, 3617.0, 3608.0, 3616.5),
            (3616.5, 3624.0, 3616.0, 3623.5),
            (3623.5, 3630.0, 3623.0, 3629.0),
            (3629.0, 3629.2, 3623.0, 3623.5),
            (3623.5, 3624.0, 3618.0, 3618.5),
            (3618.5, 3619.0, 3614.0, 3615.0),  # M15 still bearish/red at W2 low!
        ]
    )
    m15_bars = _make_bars(m15_prices)

    # M5: 55 bars forming the pullback into 3614 -> liquidity sweep to 3613.5 -> M5 MSS + 21 EMA
    m5_start = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
    m5_tuples: list[tuple[float, float, float, float]] = []
    for i in range(48):
        p = 3626.0 - i * 0.22
        m5_tuples.append((p, p + 0.7, p - 0.7, p - 0.15))
    # M5 sniper reversal sequence: sweep -> CRT -> strong displacement MSS above 21 EMA
    m5_tuples.extend(
        [
            (3615.8, 3616.2, 3614.5, 3614.8),
            (3614.8, 3615.2, 3614.0, 3614.4),
            (3614.4, 3615.0, 3613.5, 3614.6),  # Liquidity sweep + CRT manipulation!
            (3614.6, 3617.5, 3614.5, 3617.2),  # Bullish push
            (3617.2, 3620.8, 3617.0, 3620.5),  # Strong M5 displacement + MSS + FVG + > 21 EMA!
        ]
    )
    m5_bars = [
        CandleBar(
            symbol="XAUUSDm",
            timeframe="M5",
            candle_time=m5_start + timedelta(minutes=5 * idx),
            open=Decimal(f"{op:.3f}"),
            high=Decimal(f"{hi:.3f}"),
            low=Decimal(f"{lo:.3f}"),
            close=Decimal(f"{cl:.3f}"),
            volume=Decimal("180" if idx == len(m5_tuples) - 1 else "80"),
        )
        for idx, (op, hi, lo, cl) in enumerate(m5_tuples)
    ]

    strat = XauusdElliottWaveV1Strategy(price_digits=3)
    ctx = StrategyContext(
        symbol="XAUUSDm",
        now_utc=datetime(2026, 11, 3, 13, 30, tzinfo=UTC),
        current_bid=Decimal("3620.300"),
        current_ask=Decimal("3620.540"),
        max_spread=Decimal("0.600"),
        h1_bars=h1_bars,
        m15_bars=m15_bars,
        m5_bars=m5_bars,
    )
    cand = strat.evaluate(ctx)
    assert cand is not None
    assert cand.timeframe == "M5"
    assert cand.direction == "BUY"
    assert cand.indicators["mtfRoles"] == {
        "higherTimeframe": "H1",
        "countingTimeframe": "M15",
        "lowerTimeframe": "M5",
    }
    assert cand.indicators["elliottWave"]["anchorDivergences"] >= 2
    assert cand.indicators["m5"] is not None
    assert cand.stop_loss < Decimal("3613.500")
    assert cand.reward_risk >= Decimal("2.00")



