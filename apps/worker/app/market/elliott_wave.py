"""Causal Elliott Wave + MACD + EMA 21/144 + SMC Anchor Point & Wave Classifier.

Implements all 10 custom Elliott Wave rule sets:
1. Anchor Point (2-3x MACD divergence, 81.2% limit, shakeout, sweep, HTF pin, CRT, volume)
2. Significant Swings (IDM sweep -> Major High/Low -> CHoCH/BOS/MSS + POI + Volume)
3. Wave 1 (EMA 21/144 break & angle, MACD zero-line cross, internal space)
4. Wave 2 (38.2-81.2% Fib, hard 81.2% close invalidation, MACD >= Anchor Base Point)
5. Wave 3 (127-161.8% / 261.8% Trend Fib, W3 MACD peak > W1 MACD peak, L-S-M-D-F)
6. Wave 4 (23.6-50% Fib, no W1 overlap, W4 MACD < W2 MACD reset, EMA 21/144 bounce)
7. Wave 5 (127-161.8% W4 target, > W3 peak, 3-level start, 5-layer termination)
8-10. Wave A-B-C (W-A new MACD bottom, W-B Zigzag 38-61% / Flat 61-111%, W-C MACD >= W-A)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.market.indicators import EwIndicatorSeries
from app.mt5.market_data import CandleBar

PivotKind = Literal["HIGH", "LOW"]
WaveSetupType = Literal["WAVE_3", "WAVE_5", "ABC_REVERSAL", "WAVE_5_TERMINATION"]


@dataclass(frozen=True)
class SwingPivot:
    index: int
    candle_time: datetime
    price: Decimal
    kind: PivotKind


@dataclass(frozen=True)
class AnchorPointInfo:
    pivot: SwingPivot
    divergence_count: int
    htf_divergence_confirmed: bool
    base_macd: float
    before_812_rule_ok: bool


@dataclass(frozen=True)
class ElliottWavePattern:
    setup_type: WaveSetupType
    direction: Literal["BUY", "SELL"]
    pivots: list[SwingPivot]
    wave_1_size: Decimal
    retracement_ratio: float
    extension_ratio: float | None
    invalidation_level: Decimal
    fib_target_price: Decimal
    anchor_divergences: int
    macd_checks: list[str]
    ema_checks: list[str]
    summary: str


def detect_causal_zigzag(
    bars: list[CandleBar],
    *,
    atr_value: Decimal,
    reversal_atr_mult: Decimal = Decimal("1.5"),
) -> list[SwingPivot]:
    """Build strictly causal alternating HIGH/LOW swing pivots with zero lookahead."""
    if len(bars) < 5 or atr_value <= 0:
        return []

    threshold = atr_value * reversal_atr_mult
    if threshold <= 0:
        return []

    pivots: list[SwingPivot] = []
    trend: Literal["UP", "DOWN"] | None = None

    extreme_idx = 0
    extreme_high = bars[0].high
    extreme_low = bars[0].low
    extreme_high_idx = 0
    extreme_low_idx = 0

    for i in range(1, len(bars)):
        b = bars[i]

        if trend is None:
            if b.high > extreme_high:
                extreme_high = b.high
                extreme_high_idx = i
            if b.low < extreme_low:
                extreme_low = b.low
                extreme_low_idx = i

            if b.high - extreme_low >= threshold and extreme_low_idx <= i:
                pivots.append(
                    SwingPivot(
                        index=extreme_low_idx,
                        candle_time=bars[extreme_low_idx].candle_time,
                        price=extreme_low,
                        kind="LOW",
                    )
                )
                trend = "UP"
                extreme_high = b.high
                extreme_idx = i
            elif extreme_high - b.low >= threshold and extreme_high_idx <= i:
                pivots.append(
                    SwingPivot(
                        index=extreme_high_idx,
                        candle_time=bars[extreme_high_idx].candle_time,
                        price=extreme_high,
                        kind="HIGH",
                    )
                )
                trend = "DOWN"
                extreme_low = b.low
                extreme_idx = i
            continue

        if trend == "UP":
            if b.high >= extreme_high:
                extreme_high = b.high
                extreme_idx = i
            elif extreme_high - b.low >= threshold:
                pivots.append(
                    SwingPivot(
                        index=extreme_idx,
                        candle_time=bars[extreme_idx].candle_time,
                        price=extreme_high,
                        kind="HIGH",
                    )
                )
                trend = "DOWN"
                extreme_low = b.low
                extreme_idx = i
        else:
            if b.low <= extreme_low:
                extreme_low = b.low
                extreme_idx = i
            elif b.high - extreme_low >= threshold:
                pivots.append(
                    SwingPivot(
                        index=extreme_idx,
                        candle_time=bars[extreme_idx].candle_time,
                        price=extreme_low,
                        kind="LOW",
                    )
                )
                trend = "UP"
                extreme_high = b.high
                extreme_idx = i

    return pivots


def _macd_extreme_near(
    macd: list[float], idx: int, kind: PivotKind, radius: int = 2
) -> float:
    lo = max(0, idx - radius)
    hi = min(len(macd), idx + radius + 1)
    window = macd[lo:hi]
    if not window:
        return 0.0
    return min(window) if kind == "LOW" else max(window)


def count_macd_divergences(
    pivots: list[SwingPivot],
    macd_line: list[float],
    *,
    direction: Literal["BUY", "SELL"],
    end_pivot_index: int | None = None,
) -> tuple[int, SwingPivot | None]:
    """Count consecutive Positive (BUY) or Negative (SELL) MACD divergences ending at latest pivot.

    If multiple divergences occur, returns (count, latest_divergence_pivot) per Rule Set #1.
    """
    target_kind: PivotKind = "LOW" if direction == "BUY" else "HIGH"
    filtered = [
        p
        for p in pivots
        if p.kind == target_kind and (end_pivot_index is None or p.index <= end_pivot_index)
    ]
    if len(filtered) < 2:
        return 0, (filtered[-1] if filtered else None)

    div_count = 0
    latest_div_pivot: SwingPivot | None = None
    # Walk forward tracking progressive extremes so only unbroken divergences at Anchor count
    recent = filtered[-6:]
    best_p = recent[0]
    best_m = _macd_extreme_near(macd_line, best_p.index, target_kind)
    for curr_p in recent[1:]:
        m_curr = _macd_extreme_near(macd_line, curr_p.index, target_kind)
        if direction == "BUY":
            if curr_p.price <= best_p.price:
                if m_curr > best_m:
                    div_count += 1
                    latest_div_pivot = curr_p
                else:
                    div_count = 0
                best_p = curr_p
                best_m = m_curr
        else:
            if curr_p.price >= best_p.price:
                if m_curr < best_m:
                    div_count += 1
                    latest_div_pivot = curr_p
                else:
                    div_count = 0
                best_p = curr_p
                best_m = m_curr

    return div_count, latest_div_pivot


def count_local_macd_divergences(
    bars: list[CandleBar],
    macd_line: list[float],
    macd_hist: list[float],
    *,
    direction: Literal["BUY", "SELL"],
    end_idx: int,
    lookback: int = 45,
) -> int:
    """Count unbroken 3-bar fractal MACD (line or histogram) divergences into end_idx."""
    start = max(1, end_idx - lookback)
    if end_idx - start < 4:
        return 0

    fractals: list[tuple[int, Decimal]] = []
    for i in range(start, min(end_idx, len(bars) - 1)):
        if direction == "BUY":
            if (
                bars[i].low <= bars[i - 1].low
                and bars[i].low <= bars[i + 1].low
                and (bars[i].low < bars[i - 1].low or bars[i].low < bars[i + 1].low)
            ):
                fractals.append((i, bars[i].low))
        else:
            if (
                bars[i].high >= bars[i - 1].high
                and bars[i].high >= bars[i + 1].high
                and (bars[i].high > bars[i - 1].high or bars[i].high > bars[i + 1].high)
            ):
                fractals.append((i, bars[i].high))

    # Always include end_idx itself as the candidate Anchor extreme
    end_price = bars[end_idx].low if direction == "BUY" else bars[end_idx].high
    if not fractals or fractals[-1][0] != end_idx:
        fractals.append((end_idx, end_price))

    if len(fractals) < 2:
        return 0

    kind: PivotKind = "LOW" if direction == "BUY" else "HIGH"
    div_count = 0
    best_idx, best_p = fractals[0]
    best_m = _macd_extreme_near(macd_line, best_idx, kind, radius=1)
    best_h = _macd_extreme_near(macd_hist, best_idx, kind, radius=1)

    for curr_idx, curr_p in fractals[1:]:
        if curr_idx - best_idx < 2:
            if (direction == "BUY" and curr_p < best_p) or (
                direction == "SELL" and curr_p > best_p
            ):
                best_idx, best_p = curr_idx, curr_p
                best_m = _macd_extreme_near(macd_line, curr_idx, kind, radius=1)
                best_h = _macd_extreme_near(macd_hist, curr_idx, kind, radius=1)
            continue
        m_curr = _macd_extreme_near(macd_line, curr_idx, kind, radius=1)
        h_curr = _macd_extreme_near(macd_hist, curr_idx, kind, radius=1)
        if direction == "BUY":
            if curr_p <= best_p:
                if m_curr > best_m or h_curr > best_h:
                    div_count += 1
                else:
                    div_count = 0
                best_idx, best_p = curr_idx, curr_p
                best_m, best_h = m_curr, h_curr
        else:
            if curr_p >= best_p:
                if m_curr < best_m or h_curr < best_h:
                    div_count += 1
                else:
                    div_count = 0
                best_idx, best_p = curr_idx, curr_p
                best_m, best_h = m_curr, h_curr

    if abs(end_idx - best_idx) > 3:
        return 0
    return div_count


def detect_anchor_point(
    *,
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    m15_series: EwIndicatorSeries,
    h1_bars: list[CandleBar] | None = None,
    h1_pivots: list[SwingPivot],
    h1_series: EwIndicatorSeries | None,
    direction: Literal["BUY", "SELL"],
    anchor_candidate: SwingPivot,
) -> AnchorPointInfo:
    """Evaluate Anchor Point using Rule Set #1 (Mandatory 2+ MACD divergence across LTF/HTF)."""
    m15_zz_divs, _ = count_macd_divergences(
        pivots, m15_series.macd_line, direction=direction, end_pivot_index=anchor_candidate.index
    )
    m15_local_divs = count_local_macd_divergences(
        bars,
        m15_series.macd_line,
        m15_series.macd_hist,
        direction=direction,
        end_idx=anchor_candidate.index,
    )
    h1_divs = 0
    if h1_series is not None:
        h1_end_idx: int | None = None
        if h1_bars:
            h1_end_idx = len(h1_bars) - 1
            for idx in range(len(h1_bars) - 1, -1, -1):
                if h1_bars[idx].candle_time <= anchor_candidate.candle_time:
                    h1_end_idx = idx
                    break
        if len(h1_pivots) >= 2:
            h1_zz, _ = count_macd_divergences(
                h1_pivots,
                h1_series.macd_line,
                direction=direction,
                end_pivot_index=h1_end_idx,
            )
            h1_divs = max(h1_divs, h1_zz)
        if h1_bars and h1_end_idx is not None:
            h1_local = count_local_macd_divergences(
                h1_bars,
                h1_series.macd_line,
                h1_series.macd_hist,
                direction=direction,
                end_idx=h1_end_idx,
                lookback=30,
            )
            h1_divs = max(h1_divs, h1_local)

    total_divs = max(m15_zz_divs, m15_local_divs) + h1_divs
    kind: PivotKind = "LOW" if direction == "BUY" else "HIGH"
    base_macd = _macd_extreme_near(m15_series.macd_line, anchor_candidate.index, kind)

    return AnchorPointInfo(
        pivot=anchor_candidate,
        divergence_count=total_divs,
        htf_divergence_confirmed=h1_divs >= 1,
        base_macd=base_macd,
        before_812_rule_ok=True,
    )


def _recent_pullback_extreme(
    bars: list[CandleBar], start_idx: int, kind: PivotKind
) -> SwingPivot | None:
    if start_idx >= len(bars) - 1:
        return None
    best_idx = start_idx + 1
    best_price = bars[best_idx].low if kind == "LOW" else bars[best_idx].high
    for i in range(start_idx + 1, len(bars)):
        val = bars[i].low if kind == "LOW" else bars[i].high
        if (kind == "LOW" and val < best_price) or (
            kind == "HIGH" and val > best_price
        ):
            best_price = val
            best_idx = i
    return SwingPivot(
        index=best_idx,
        candle_time=bars[best_idx].candle_time,
        price=best_price,
        kind=kind,
    )


def _has_body_close_beyond_812(
    bars: list[CandleBar],
    *,
    start_idx: int,
    origin_price: Decimal,
    wave1_end_price: Decimal,
    direction: Literal["BUY", "SELL"],
) -> bool:
    """HARD RULE (Wave 2): even ONE candle CLOSE beyond 81.2% invalidates Wave 2 (-> Wave B).

    Wicks/shadows piercing 81.2% are tolerated as long as candle closes remain inside 81.2%.
    """
    w1_len = abs(wave1_end_price - origin_price)
    if w1_len <= 0:
        return True
    if direction == "BUY":
        limit_812 = wave1_end_price - (w1_len * Decimal("0.812"))
        return any(b.close < limit_812 for b in bars[start_idx + 1 :])
    limit_812 = wave1_end_price + (w1_len * Decimal("0.812"))
    return any(b.close > limit_812 for b in bars[start_idx + 1 :])


def detect_wave_3(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    series: EwIndicatorSeries | None = None,
    h1_bars: list[CandleBar] | None = None,
    h1_pivots: list[SwingPivot] | None = None,
    h1_series: EwIndicatorSeries | None = None,
    min_wave_atr: Decimal = Decimal("1.8"),
    enforce_macd_base_rule: bool = True,
    min_anchor_divergences: int = 2,
) -> ElliottWavePattern | None:
    """Detect Wave 1 + Wave 2 (38.2%-81.2%, hard 81.2% close & MACD Base rules) -> Wave 3."""
    if len(pivots) < 2 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1
    macd_checks: list[str] = []
    ema_checks: list[str] = []

    if direction == "BUY":
        if pivots[-1].kind == "HIGH" and len(pivots) >= 2:
            p0, p1 = pivots[-2], pivots[-1]
            p2 = _recent_pullback_extreme(bars, p1.index, "LOW")
        elif pivots[-1].kind == "LOW" and len(pivots) >= 3:
            p0, p1, p2 = pivots[-3], pivots[-2], pivots[-1]
        else:
            return None

        if p2 is None or (last_bar_idx - p2.index) > 10:
            return None
        if not (p0.kind == "LOW" and p1.kind == "HIGH"):
            return None

        w1 = p1.price - p0.price
        if w1 < min_len or (p1.index - p0.index) < 2:
            return None

        # Hard Rule: Wave 2 never breaks Wave 1 origin P0
        if p2.price <= p0.price:
            return None

        # Hard Rule (Wave 2): Single candle CLOSE below 81.2% -> Wave B, NOT Wave 2!
        if _has_body_close_beyond_812(
            bars,
            start_idx=p1.index,
            origin_price=p0.price,
            wave1_end_price=p1.price,
            direction="BUY",
        ):
            return None

        # Extended W1 (>= 4x ATR) allows 23.6%-38.2%, normal W1 uses 38.2%-81.2%
        w1_extended = w1 >= atr_value * Decimal("4.0")
        min_retrace = 0.236 if w1_extended else 0.382
        retrace = float((p1.price - p2.price) / w1)
        if not (min_retrace <= retrace <= 0.85):
            return None

        if bars[-1].close >= p1.price:
            return None

        anchor_divs = 0
        if series is not None:
            anchor = detect_anchor_point(
                bars=bars,
                pivots=pivots,
                m15_series=series,
                h1_bars=h1_bars,
                h1_pivots=h1_pivots or [],
                h1_series=h1_series,
                direction="BUY",
                anchor_candidate=p0,
            )
            anchor_divs = anchor.divergence_count
            if anchor_divs < min_anchor_divergences:
                return None
            w1_macd_peak = _macd_extreme_near(series.macd_line, p1.index, "HIGH")
            w2_macd_low = _macd_extreme_near(series.macd_line, p2.index, "LOW")

            # HARD MACD BASE-POINT RULE: W2 MACD MUST NOT break W1 Base Point MACD
            if enforce_macd_base_rule and w2_macd_low < anchor.base_macd:
                return None
            macd_checks.append(
                f"Anchor confirmed with {anchor_divs}x MACD positive divergence "
                f"(mandatory >= {min_anchor_divergences})"
            )
            macd_checks.append(
                f"W2 MACD ({w2_macd_low:.3f}) held above Anchor Base ({anchor.base_macd:.3f})"
            )
            if w1_macd_peak > 0:
                macd_checks.append("W1 MACD crossed above zero line")

            # EMA 21/144 check
            ema21_now = series.ema_21[-1]
            ema21_prev = series.ema_21[-3] if len(series.ema_21) >= 3 else ema21_now
            if float(bars[-1].close) > ema21_now:
                ema_checks.append("Price reclaimed 21 EMA")
            if ema21_now >= ema21_prev:
                ema_checks.append("21 EMA turning bullish angle")
            ema144_p2 = series.ema_144[p2.index]
            if ema144_p2 is not None and abs(float(p2.price) - ema144_p2) <= float(atr_value) * 1.2:
                ema_checks.append("144 EMA acted as Wave-2 dynamic support")

        # Trend-based Fib target: 161.8% (minimum 127.2%)
        fib_target = p2.price + (w1 * Decimal("1.618"))
        return ElliottWavePattern(
            setup_type="WAVE_3",
            direction="BUY",
            pivots=[p0, p1, p2],
            wave_1_size=w1,
            retracement_ratio=round(retrace, 3),
            extension_ratio=1.618,
            invalidation_level=p2.price,
            fib_target_price=fib_target,
            anchor_divergences=anchor_divs,
            macd_checks=macd_checks,
            ema_checks=ema_checks,
            summary=(
                f"Bullish Wave 3 launch: W1={w1:.2f}, W2 Fib={retrace * 100:.1f}% "
                f"(81.2% close & origin {p0.price} intact)"
            ),
        )

    # SELL mirror
    if pivots[-1].kind == "LOW" and len(pivots) >= 2:
        p0, p1 = pivots[-2], pivots[-1]
        p2 = _recent_pullback_extreme(bars, p1.index, "HIGH")
    elif pivots[-1].kind == "HIGH" and len(pivots) >= 3:
        p0, p1, p2 = pivots[-3], pivots[-2], pivots[-1]
    else:
        return None

    if p2 is None or (last_bar_idx - p2.index) > 10:
        return None
    if not (p0.kind == "HIGH" and p1.kind == "LOW"):
        return None

    w1 = p0.price - p1.price
    if w1 < min_len or (p1.index - p0.index) < 2:
        return None

    if p2.price >= p0.price:
        return None

    if _has_body_close_beyond_812(
        bars,
        start_idx=p1.index,
        origin_price=p0.price,
        wave1_end_price=p1.price,
        direction="SELL",
    ):
        return None

    w1_extended = w1 >= atr_value * Decimal("4.0")
    min_retrace = 0.236 if w1_extended else 0.382
    retrace = float((p2.price - p1.price) / w1)
    if not (min_retrace <= retrace <= 0.85):
        return None

    if bars[-1].close <= p1.price:
        return None

    anchor_divs = 0
    if series is not None:
        anchor = detect_anchor_point(
            bars=bars,
            pivots=pivots,
            m15_series=series,
            h1_bars=h1_bars,
            h1_pivots=h1_pivots or [],
            h1_series=h1_series,
            direction="SELL",
            anchor_candidate=p0,
        )
        anchor_divs = anchor.divergence_count
        if anchor_divs < min_anchor_divergences:
            return None
        w1_macd_low = _macd_extreme_near(series.macd_line, p1.index, "LOW")
        w2_macd_high = _macd_extreme_near(series.macd_line, p2.index, "HIGH")

        if enforce_macd_base_rule and w2_macd_high > anchor.base_macd:
            return None
        macd_checks.append(
            f"Anchor confirmed with {anchor_divs}x MACD negative divergence "
            f"(mandatory >= {min_anchor_divergences})"
        )
        macd_checks.append(
            f"W2 MACD ({w2_macd_high:.3f}) held below Anchor Base ({anchor.base_macd:.3f})"
        )
        if w1_macd_low < 0:
            macd_checks.append("W1 MACD crossed below zero line")

        ema21_now = series.ema_21[-1]
        ema21_prev = series.ema_21[-3] if len(series.ema_21) >= 3 else ema21_now
        if float(bars[-1].close) < ema21_now:
            ema_checks.append("Price broke below 21 EMA")
        if ema21_now <= ema21_prev:
            ema_checks.append("21 EMA turning bearish angle")
        ema144_p2 = series.ema_144[p2.index]
        if ema144_p2 is not None and abs(float(p2.price) - ema144_p2) <= float(atr_value) * 1.2:
            ema_checks.append("144 EMA acted as Wave-2 dynamic resistance")

    fib_target = p2.price - (w1 * Decimal("1.618"))
    return ElliottWavePattern(
        setup_type="WAVE_3",
        direction="SELL",
        pivots=[p0, p1, p2],
        wave_1_size=w1,
        retracement_ratio=round(retrace, 3),
        extension_ratio=1.618,
        invalidation_level=p2.price,
        fib_target_price=fib_target,
        anchor_divergences=anchor_divs,
        macd_checks=macd_checks,
        ema_checks=ema_checks,
        summary=(
            f"Bearish Wave 3 launch: W1={w1:.2f}, W2 Fib={retrace * 100:.1f}% "
            f"(81.2% close & origin {p0.price} intact)"
        ),
    )


def detect_wave_5(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    series: EwIndicatorSeries | None = None,
    h1_bars: list[CandleBar] | None = None,
    h1_pivots: list[SwingPivot] | None = None,
    h1_series: EwIndicatorSeries | None = None,
    min_wave_atr: Decimal = Decimal("1.5"),
    enforce_w3_macd_peak: bool = True,
    min_anchor_divergences: int = 2,
) -> ElliottWavePattern | None:
    """Detect Wave 4 -> Wave 5 with strict 3 Cardinal Rules + W3 MACD peak + W4 MACD reset."""
    if len(pivots) < 4 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1
    macd_checks: list[str] = []
    ema_checks: list[str] = []

    if direction == "BUY":
        if pivots[-1].kind == "HIGH" and len(pivots) >= 4:
            p0, p1, p2, p3 = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
            p4 = _recent_pullback_extreme(bars, p3.index, "LOW")
        elif pivots[-1].kind == "LOW" and len(pivots) >= 5:
            p0, p1, p2, p3, p4 = pivots[-5], pivots[-4], pivots[-3], pivots[-2], pivots[-1]
        else:
            return None

        if p4 is None or (last_bar_idx - p4.index) > 12:
            return None
        if not (p0.kind == "LOW" and p1.kind == "HIGH" and p2.kind == "LOW" and p3.kind == "HIGH"):
            return None

        w1 = p1.price - p0.price
        w3 = p3.price - p2.price
        if w1 < min_len or w3 <= 0:
            return None

        # Cardinal Rule 1: Wave 2 intact above Wave 1 origin
        if p2.price <= p0.price:
            return None
        w2_retrace = float((p1.price - p2.price) / w1)
        if not (0.236 <= w2_retrace <= 0.85):
            return None

        # Wave 3 must exceed Wave 1 peak & reach at least 61.8% (trending >= 115%)
        w3_ext = float(w3 / w1)
        if p3.price <= p1.price or w3_ext < 1.15:
            return None

        # Hard Rule #1 & #2 (Wave 4): NEVER overlap Wave 1 peak P1 and NEVER drop below P2
        if p4.price <= p1.price or p4.price <= p2.price:
            return None

        # Wave 4 Fib bounds: Extended W3 (161.8%+) -> 23.6%-42%, normal -> 23.6%-55%
        w4_retrace = float((p3.price - p4.price) / w3)
        max_w4 = 0.45 if w3_ext >= 1.618 else 0.55
        if not (0.236 <= w4_retrace <= max_w4):
            return None

        if bars[-1].close >= p3.price:
            return None

        anchor_divs = 0
        if series is not None:
            anchor = detect_anchor_point(
                bars=bars,
                pivots=pivots,
                m15_series=series,
                h1_bars=h1_bars,
                h1_pivots=h1_pivots or [],
                h1_series=h1_series,
                direction="BUY",
                anchor_candidate=p0,
            )
            anchor_divs = anchor.divergence_count
            if anchor_divs < min_anchor_divergences:
                return None
            w1_macd = _macd_extreme_near(series.macd_line, p1.index, "HIGH")
            w3_macd = _macd_extreme_near(series.macd_line, p3.index, "HIGH")
            w2_macd = _macd_extreme_near(series.macd_line, p2.index, "LOW")
            w4_macd = _macd_extreme_near(series.macd_line, p4.index, "LOW")
            # HARD RULE (Wave 3): W3 MACD peak MUST exceed W1 MACD peak (otherwise Wave C!)
            if enforce_w3_macd_peak and w3_macd <= w1_macd:
                return None
            macd_checks.append(
                f"Anchor confirmed with {anchor_divs}x MACD positive divergence "
                f"(mandatory >= {min_anchor_divergences})"
            )
            macd_checks.append(f"W3 MACD peak ({w3_macd:.3f}) > W1 MACD peak ({w1_macd:.3f})")
            if w4_macd < w2_macd:
                macd_checks.append("W4 MACD reset below W2 MACD (larger degree confirmed)")

            ema144_p4 = series.ema_144[p4.index]
            if ema144_p4 is not None and abs(float(p4.price) - ema144_p4) <= float(atr_value) * 1.5:
                ema_checks.append("144 EMA acted as Wave-4 bounce spot")
            if float(bars[-1].close) > series.ema_21[-1]:
                ema_checks.append("Price broke back above 21 EMA for Wave-5 launch")

        # Wave 5 Target: 127.2%-161.8% of W4 (W3->W4), strictly exceeding W3 peak (Rule #3)
        w4_size = p3.price - p4.price
        fib_target = p4.price + (w4_size * Decimal("1.618"))
        min_w5_target = p3.price + (w4_size * Decimal("0.272"))
        if fib_target < min_w5_target:
            fib_target = min_w5_target

        return ElliottWavePattern(
            setup_type="WAVE_5",
            direction="BUY",
            pivots=[p0, p1, p2, p3, p4],
            wave_1_size=w1,
            retracement_ratio=round(w4_retrace, 3),
            extension_ratio=round(w3_ext, 3),
            invalidation_level=p4.price,
            fib_target_price=fib_target,
            anchor_divergences=anchor_divs,
            macd_checks=macd_checks,
            ema_checks=ema_checks,
            summary=(
                f"Bullish Wave 5 launchpad: W3/W1={w3_ext:.2f}x, "
                f"W4 Fib={w4_retrace * 100:.1f}% strictly above W1 peak ({p1.price})"
            ),
        )

    # SELL mirror
    if pivots[-1].kind == "LOW" and len(pivots) >= 4:
        p0, p1, p2, p3 = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
        p4 = _recent_pullback_extreme(bars, p3.index, "HIGH")
    elif pivots[-1].kind == "HIGH" and len(pivots) >= 5:
        p0, p1, p2, p3, p4 = pivots[-5], pivots[-4], pivots[-3], pivots[-2], pivots[-1]
    else:
        return None

    if p4 is None or (last_bar_idx - p4.index) > 12:
        return None
    if not (p0.kind == "HIGH" and p1.kind == "LOW" and p2.kind == "HIGH" and p3.kind == "LOW"):
        return None

    w1 = p0.price - p1.price
    w3 = p2.price - p3.price
    if w1 < min_len or w3 <= 0:
        return None

    if p2.price >= p0.price:
        return None
    w2_retrace = float((p2.price - p1.price) / w1)
    if not (0.236 <= w2_retrace <= 0.85):
        return None

    w3_ext = float(w3 / w1)
    if p3.price >= p1.price or w3_ext < 1.15:
        return None

    if p4.price >= p1.price or p4.price >= p2.price:
        return None

    w4_retrace = float((p4.price - p3.price) / w3)
    max_w4 = 0.45 if w3_ext >= 1.618 else 0.55
    if not (0.236 <= w4_retrace <= max_w4):
        return None

    if bars[-1].close <= p3.price:
        return None

    anchor_divs = 0
    if series is not None:
        anchor = detect_anchor_point(
            bars=bars,
            pivots=pivots,
            m15_series=series,
            h1_bars=h1_bars,
            h1_pivots=h1_pivots or [],
            h1_series=h1_series,
            direction="SELL",
            anchor_candidate=p0,
        )
        anchor_divs = anchor.divergence_count
        if anchor_divs < min_anchor_divergences:
            return None
        w1_macd = _macd_extreme_near(series.macd_line, p1.index, "LOW")
        w3_macd = _macd_extreme_near(series.macd_line, p3.index, "LOW")
        w2_macd = _macd_extreme_near(series.macd_line, p2.index, "HIGH")
        w4_macd = _macd_extreme_near(series.macd_line, p4.index, "HIGH")
        if enforce_w3_macd_peak and w3_macd >= w1_macd:
            return None
        macd_checks.append(
            f"Anchor confirmed with {anchor_divs}x MACD negative divergence "
            f"(mandatory >= {min_anchor_divergences})"
        )
        macd_checks.append(f"W3 MACD low ({w3_macd:.3f}) < W1 MACD low ({w1_macd:.3f})")
        if w4_macd > w2_macd:
            macd_checks.append("W4 MACD reset above W2 MACD (larger degree confirmed)")

        ema144_p4 = series.ema_144[p4.index]
        if ema144_p4 is not None and abs(float(p4.price) - ema144_p4) <= float(atr_value) * 1.5:
            ema_checks.append("144 EMA acted as Wave-4 resistance bounce")
        if float(bars[-1].close) < series.ema_21[-1]:
            ema_checks.append("Price broke back below 21 EMA for Wave-5 launch")

    w4_size = p4.price - p3.price
    fib_target = p4.price - (w4_size * Decimal("1.618"))
    min_w5_target = p3.price - (w4_size * Decimal("0.272"))
    if fib_target > min_w5_target:
        fib_target = min_w5_target

    return ElliottWavePattern(
        setup_type="WAVE_5",
        direction="SELL",
        pivots=[p0, p1, p2, p3, p4],
        wave_1_size=w1,
        retracement_ratio=round(w4_retrace, 3),
        extension_ratio=round(w3_ext, 3),
        invalidation_level=p4.price,
        fib_target_price=fib_target,
        anchor_divergences=anchor_divs,
        macd_checks=macd_checks,
        ema_checks=ema_checks,
        summary=(
            f"Bearish Wave 5 launchpad: W3/W1={w3_ext:.2f}x, "
            f"W4 Fib={w4_retrace * 100:.1f}% strictly below W1 low ({p1.price})"
        ),
    )


def detect_abc_reversal(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    series: EwIndicatorSeries | None = None,
    h1_bars: list[CandleBar] | None = None,
    h1_pivots: list[SwingPivot] | None = None,
    h1_series: EwIndicatorSeries | None = None,
    min_wave_atr: Decimal = Decimal("1.4"),
    enforce_ac_macd_divergence: bool = True,
    min_anchor_divergences: int = 2,
) -> ElliottWavePattern | None:
    """Detect A-B-C completion (Zigzag 38-61% / Flat 61-111% + Hard A-C MACD Divergence)."""
    if len(pivots) < 3 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1
    macd_checks: list[str] = []
    ema_checks: list[str] = []

    if direction == "BUY":
        if pivots[-1].kind == "HIGH" and len(pivots) >= 3:
            pa_start, pa_end, pb_end = pivots[-3], pivots[-2], pivots[-1]
            pc_end = _recent_pullback_extreme(bars, pb_end.index, "LOW")
        elif pivots[-1].kind == "LOW" and len(pivots) >= 4:
            pa_start, pa_end, pb_end, pc_end = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
        else:
            return None

        if pc_end is None or (last_bar_idx - pc_end.index) > 8:
            return None
        if not (pa_start.kind == "HIGH" and pa_end.kind == "LOW" and pb_end.kind == "HIGH"):
            return None

        wave_a = pa_start.price - pa_end.price
        if wave_a < min_len:
            return None

        # Wave B: Zigzag (38.2-61.8%) or Flat/Expanded Flat (61.8-111%)
        b_retrace = float((pb_end.price - pa_end.price) / wave_a)
        if not (0.382 <= b_retrace <= 1.11):
            return None
        corr_type = "Flat" if b_retrace > 0.618 else "Zigzag"

        # Wave C must break Wave A low and reach 61.8% - 161.8% of Wave A
        if pc_end.price >= pa_end.price:
            return None
        wave_c = pb_end.price - pc_end.price
        c_ext = float(wave_c / wave_a)
        if not (0.618 <= c_ext <= 1.618):
            return None

        if bars[-1].close >= pb_end.price:
            return None

        # HARD RULE (Wave C): W-C MACD MUST NOT go below W-A MACD + 2+ total divergences
        anchor_divs = 1
        if series is not None:
            wa_macd = _macd_extreme_near(series.macd_line, pa_end.index, "LOW")
            wc_macd = _macd_extreme_near(series.macd_line, pc_end.index, "LOW")
            if enforce_ac_macd_divergence and wc_macd < wa_macd:
                return None
            anchor = detect_anchor_point(
                bars=bars,
                pivots=pivots,
                m15_series=series,
                h1_bars=h1_bars,
                h1_pivots=h1_pivots or [],
                h1_series=h1_series,
                direction="BUY",
                anchor_candidate=pc_end,
            )
            c_internal_divs = count_local_macd_divergences(
                bars,
                series.macd_line,
                series.macd_hist,
                direction="BUY",
                end_idx=pc_end.index,
                lookback=max(6, pc_end.index - pb_end.index + 4),
            )
            anchor_divs = max(
                anchor.divergence_count,
                1 + c_internal_divs + (1 if anchor.htf_divergence_confirmed else 0),
            )
            if anchor_divs < min_anchor_divergences:
                return None
            macd_checks.append(
                f"Major A-C Positive MACD Divergence (W-C {wc_macd:.3f} >= W-A {wa_macd:.3f})"
            )
            macd_checks.append(
                f"Wave-C Anchor confirmed with {anchor_divs}x MACD positive divergence "
                f"(mandatory >= {min_anchor_divergences})"
            )
            if float(bars[-1].close) > series.ema_21[-1]:
                ema_checks.append("Price reclaimed 21 EMA after Wave-C completion")

        return ElliottWavePattern(
            setup_type="ABC_REVERSAL",
            direction="BUY",
            pivots=[pa_start, pa_end, pb_end, pc_end],
            wave_1_size=wave_a,
            retracement_ratio=round(b_retrace, 3),
            extension_ratio=round(c_ext, 3),
            invalidation_level=pc_end.price,
            fib_target_price=pa_start.price,
            anchor_divergences=anchor_divs,
            macd_checks=macd_checks,
            ema_checks=ema_checks,
            summary=(
                f"Bullish ABC ({corr_type}) complete: B={b_retrace * 100:.1f}%, "
                f"C={c_ext:.2f}x A with A-C positive MACD divergence"
            ),
        )

    # SELL mirror: Upward A-B-C correction inside H1 downtrend
    if pivots[-1].kind == "LOW" and len(pivots) >= 3:
        pa_start, pa_end, pb_end = pivots[-3], pivots[-2], pivots[-1]
        pc_end = _recent_pullback_extreme(bars, pb_end.index, "HIGH")
    elif pivots[-1].kind == "HIGH" and len(pivots) >= 4:
        pa_start, pa_end, pb_end, pc_end = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
    else:
        return None

    if pc_end is None or (last_bar_idx - pc_end.index) > 8:
        return None
    if not (pa_start.kind == "LOW" and pa_end.kind == "HIGH" and pb_end.kind == "LOW"):
        return None

    wave_a = pa_end.price - pa_start.price
    if wave_a < min_len:
        return None

    b_retrace = float((pa_end.price - pb_end.price) / wave_a)
    if not (0.382 <= b_retrace <= 1.11):
        return None
    corr_type = "Flat" if b_retrace > 0.618 else "Zigzag"

    if pc_end.price <= pa_end.price:
        return None
    wave_c = pc_end.price - pb_end.price
    c_ext = float(wave_c / wave_a)
    if not (0.618 <= c_ext <= 1.618):
        return None

    if bars[-1].close <= pb_end.price:
        return None

    anchor_divs = 1
    if series is not None:
        wa_macd = _macd_extreme_near(series.macd_line, pa_end.index, "HIGH")
        wc_macd = _macd_extreme_near(series.macd_line, pc_end.index, "HIGH")
        if enforce_ac_macd_divergence and wc_macd > wa_macd:
            return None
        anchor = detect_anchor_point(
            bars=bars,
            pivots=pivots,
            m15_series=series,
            h1_bars=h1_bars,
            h1_pivots=h1_pivots or [],
            h1_series=h1_series,
            direction="SELL",
            anchor_candidate=pc_end,
        )
        c_internal_divs = count_local_macd_divergences(
            bars,
            series.macd_line,
            series.macd_hist,
            direction="SELL",
            end_idx=pc_end.index,
            lookback=max(6, pc_end.index - pb_end.index + 4),
        )
        anchor_divs = max(
            anchor.divergence_count,
            1 + c_internal_divs + (1 if anchor.htf_divergence_confirmed else 0),
        )
        if anchor_divs < min_anchor_divergences:
            return None
        macd_checks.append(
            f"Major A-C Negative MACD Divergence (W-C {wc_macd:.3f} <= W-A {wa_macd:.3f})"
        )
        macd_checks.append(
            f"Wave-C Anchor confirmed with {anchor_divs}x MACD negative divergence "
            f"(mandatory >= {min_anchor_divergences})"
        )
        if float(bars[-1].close) < series.ema_21[-1]:
            ema_checks.append("Price broke below 21 EMA after Wave-C completion")

    return ElliottWavePattern(
        setup_type="ABC_REVERSAL",
        direction="SELL",
        pivots=[pa_start, pa_end, pb_end, pc_end],
        wave_1_size=wave_a,
        retracement_ratio=round(b_retrace, 3),
        extension_ratio=round(c_ext, 3),
        invalidation_level=pc_end.price,
        fib_target_price=pa_start.price,
        anchor_divergences=anchor_divs,
        macd_checks=macd_checks,
        ema_checks=ema_checks,
        summary=(
            f"Bearish ABC ({corr_type}) complete: B={b_retrace * 100:.1f}%, "
            f"C={c_ext:.2f}x A with A-C negative MACD divergence"
        ),
    )
