"""Causal (non-repainting) ZigZag pivot detector and Elliott Wave pattern classifier."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.mt5.market_data import CandleBar

PivotKind = Literal["HIGH", "LOW"]
WaveSetupType = Literal["WAVE_3", "WAVE_5", "ABC_REVERSAL"]


@dataclass(frozen=True)
class SwingPivot:
    index: int
    candle_time: datetime
    price: Decimal
    kind: PivotKind


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
    summary: str


def detect_causal_zigzag(
    bars: list[CandleBar],
    *,
    atr_value: Decimal,
    reversal_atr_mult: Decimal = Decimal("1.5"),
) -> list[SwingPivot]:
    """Build strictly causal alternating HIGH/LOW swing pivots with zero lookahead.

    A HIGH pivot is only recorded once a subsequent bar drops by >= reversal_threshold
    from that high. A LOW pivot is only recorded once a subsequent bar rises by
    >= reversal_threshold from that low.
    """
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
        else:  # trend == "DOWN"
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


def _recent_pullback_extreme(
    bars: list[CandleBar], start_idx: int, kind: PivotKind
) -> SwingPivot | None:
    """Find the lowest low (for LOW) or highest high (for HIGH) after start_idx."""
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


def detect_wave_5(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    min_wave_atr: Decimal = Decimal("1.5"),
) -> ElliottWavePattern | None:
    """Detect Wave 1-2-3-4 impulse completion ready for Wave 5 (strict 3 Cardinal Rules)."""
    if len(pivots) < 4 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1

    if direction == "BUY":
        # Need P0(LOW) -> P1(HIGH) -> P2(LOW) -> P3(HIGH) + developing/confirmed P4(LOW)
        if pivots[-1].kind == "HIGH" and len(pivots) >= 4:
            p0, p1, p2, p3 = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
            p4 = _recent_pullback_extreme(bars, p3.index, "LOW")
        elif pivots[-1].kind == "LOW" and len(pivots) >= 5:
            p0, p1, p2, p3, p4 = (
                pivots[-5],
                pivots[-4],
                pivots[-3],
                pivots[-2],
                pivots[-1],
            )
        else:
            return None

        if p4 is None or (last_bar_idx - p4.index) > 10:
            return None
        if not (p0.kind == "LOW" and p1.kind == "HIGH" and p2.kind == "LOW" and p3.kind == "HIGH"):
            return None

        w1 = p1.price - p0.price
        w3 = p3.price - p2.price
        if w1 < min_len or w3 <= 0:
            return None

        # Cardinal Rule 1: Wave 2 never retraces beyond Wave 1 origin
        if p2.price <= p0.price:
            return None
        w2_retrace = float((p1.price - p2.price) / w1)
        if not (0.236 <= w2_retrace <= 0.85):
            return None

        # Cardinal Rule 2: Wave 3 must be strong (not shortest) & exceed Wave 1 peak
        if p3.price <= p1.price or w3 < w1 * Decimal("1.15"):
            return None

        # Cardinal Rule 3: Wave 4 must NOT overlap Wave 1 peak P1
        if p4.price <= p1.price:
            return None
        w4_retrace = float((p3.price - p4.price) / w3)
        if not (0.236 <= w4_retrace <= 0.55):
            return None

        # Price must still be in Wave 4 turning zone (not already past P3)
        if bars[-1].close >= p3.price:
            return None

        fib_target = p4.price + (w1 * Decimal("1.0"))
        if fib_target < p3.price:
            fib_target = p3.price + (w1 * Decimal("0.382"))

        return ElliottWavePattern(
            setup_type="WAVE_5",
            direction="BUY",
            pivots=[p0, p1, p2, p3, p4],
            wave_1_size=w1,
            retracement_ratio=round(w4_retrace, 3),
            extension_ratio=round(float(w3 / w1), 3),
            invalidation_level=p4.price,
            fib_target_price=fib_target,
            summary=(
                f"Bullish Wave 5 setup: W3/W1={float(w3 / w1):.2f}x, "
                f"W4 retrace={w4_retrace * 100:.1f}% above W1 high ({p1.price})"
            ),
        )

    # SELL mirror: P0(HIGH) -> P1(LOW) -> P2(HIGH) -> P3(LOW) -> P4(HIGH)
    if pivots[-1].kind == "LOW" and len(pivots) >= 4:
        p0, p1, p2, p3 = pivots[-4], pivots[-3], pivots[-2], pivots[-1]
        p4 = _recent_pullback_extreme(bars, p3.index, "HIGH")
    elif pivots[-1].kind == "HIGH" and len(pivots) >= 5:
        p0, p1, p2, p3, p4 = (
            pivots[-5],
            pivots[-4],
            pivots[-3],
            pivots[-2],
            pivots[-1],
        )
    else:
        return None

    if p4 is None or (last_bar_idx - p4.index) > 10:
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

    if p3.price >= p1.price or w3 < w1 * Decimal("1.15"):
        return None

    # Cardinal Rule 3: Wave 4 must NOT overlap Wave 1 low P1
    if p4.price >= p1.price:
        return None
    w4_retrace = float((p4.price - p3.price) / w3)
    if not (0.236 <= w4_retrace <= 0.55):
        return None

    if bars[-1].close <= p3.price:
        return None

    fib_target = p4.price - (w1 * Decimal("1.0"))
    if fib_target > p3.price:
        fib_target = p3.price - (w1 * Decimal("0.382"))

    return ElliottWavePattern(
        setup_type="WAVE_5",
        direction="SELL",
        pivots=[p0, p1, p2, p3, p4],
        wave_1_size=w1,
        retracement_ratio=round(w4_retrace, 3),
        extension_ratio=round(float(w3 / w1), 3),
        invalidation_level=p4.price,
        fib_target_price=fib_target,
        summary=(
            f"Bearish Wave 5 setup: W3/W1={float(w3 / w1):.2f}x, "
            f"W4 retrace={w4_retrace * 100:.1f}% below W1 low ({p1.price})"
        ),
    )


def detect_wave_3(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    min_wave_atr: Decimal = Decimal("1.8"),
) -> ElliottWavePattern | None:
    """Detect Wave 1 impulse + Wave 2 Fib 38.2%-78.6% pullback ready for Wave 3."""
    if len(pivots) < 2 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1

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
        if w1 < min_len:
            return None

        # Cardinal Rule 1: Wave 2 never breaks Wave 1 origin P0
        if p2.price <= p0.price:
            return None

        retrace = float((p1.price - p2.price) / w1)
        if not (0.382 <= retrace <= 0.786):
            return None

        # Entry must happen before price has already blown past P1
        if bars[-1].close >= p1.price:
            return None

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
            summary=(
                f"Bullish Wave 3 setup: W1={w1:.2f}, "
                f"W2 Fib retrace={retrace * 100:.1f}% (origin {p0.price} intact)"
            ),
        )

    # SELL mirror: P0(HIGH) -> P1(LOW) -> P2(HIGH)
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
    if w1 < min_len:
        return None

    if p2.price >= p0.price:
        return None

    retrace = float((p2.price - p1.price) / w1)
    if not (0.382 <= retrace <= 0.786):
        return None

    if bars[-1].close <= p1.price:
        return None

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
        summary=(
            f"Bearish Wave 3 setup: W1={w1:.2f}, "
            f"W2 Fib retrace={retrace * 100:.1f}% (origin {p0.price} intact)"
        ),
    )


def detect_abc_reversal(
    bars: list[CandleBar],
    pivots: list[SwingPivot],
    *,
    direction: Literal["BUY", "SELL"],
    atr_value: Decimal,
    min_wave_atr: Decimal = Decimal("1.4"),
) -> ElliottWavePattern | None:
    """Detect A-B-C zig-zag corrective completion back into the H1 trend."""
    if len(pivots) < 3 or atr_value <= 0:
        return None

    min_len = atr_value * min_wave_atr
    last_bar_idx = len(bars) - 1

    if direction == "BUY":
        # Downward A-B-C correction: PA_start(HIGH) -> PA_end(LOW) -> PB_end(HIGH) -> PC_end(LOW)
        if pivots[-1].kind == "HIGH" and len(pivots) >= 3:
            pa_start, pa_end, pb_end = pivots[-3], pivots[-2], pivots[-1]
            pc_end = _recent_pullback_extreme(bars, pb_end.index, "LOW")
        elif pivots[-1].kind == "LOW" and len(pivots) >= 4:
            pa_start, pa_end, pb_end, pc_end = (
                pivots[-4],
                pivots[-3],
                pivots[-2],
                pivots[-1],
            )
        else:
            return None

        if pc_end is None or (last_bar_idx - pc_end.index) > 8:
            return None
        if not (pa_start.kind == "HIGH" and pa_end.kind == "LOW" and pb_end.kind == "HIGH"):
            return None

        wave_a = pa_start.price - pa_end.price
        if wave_a < min_len:
            return None
        if pb_end.price >= pa_start.price:
            return None

        b_retrace = float((pb_end.price - pa_end.price) / wave_a)
        if not (0.382 <= b_retrace <= 0.786):
            return None

        # Wave C must break below Wave A low and reach 0.786 - 1.618 of Wave A
        if pc_end.price >= pa_end.price:
            return None
        wave_c = pb_end.price - pc_end.price
        c_ext = float(wave_c / wave_a)
        if not (0.786 <= c_ext <= 1.618):
            return None

        if bars[-1].close >= pb_end.price:
            return None

        return ElliottWavePattern(
            setup_type="ABC_REVERSAL",
            direction="BUY",
            pivots=[pa_start, pa_end, pb_end, pc_end],
            wave_1_size=wave_a,
            retracement_ratio=round(b_retrace, 3),
            extension_ratio=round(c_ext, 3),
            invalidation_level=pc_end.price,
            fib_target_price=pa_start.price,
            summary=(
                f"Bullish ABC correction complete: B={b_retrace * 100:.1f}% of A, "
                f"C={c_ext:.2f}x A reversing into H1 uptrend"
            ),
        )

    # SELL mirror: Upward A-B-C correction inside H1 downtrend
    # PA_start(LOW) -> PA_end(HIGH) -> PB_end(LOW) -> PC_end(HIGH)
    if pivots[-1].kind == "LOW" and len(pivots) >= 3:
        pa_start, pa_end, pb_end = pivots[-3], pivots[-2], pivots[-1]
        pc_end = _recent_pullback_extreme(bars, pb_end.index, "HIGH")
    elif pivots[-1].kind == "HIGH" and len(pivots) >= 4:
        pa_start, pa_end, pb_end, pc_end = (
            pivots[-4],
            pivots[-3],
            pivots[-2],
            pivots[-1],
        )
    else:
        return None

    if pc_end is None or (last_bar_idx - pc_end.index) > 8:
        return None
    if not (pa_start.kind == "LOW" and pa_end.kind == "HIGH" and pb_end.kind == "LOW"):
        return None

    wave_a = pa_end.price - pa_start.price
    if wave_a < min_len:
        return None
    if pb_end.price <= pa_start.price:
        return None

    b_retrace = float((pa_end.price - pb_end.price) / wave_a)
    if not (0.382 <= b_retrace <= 0.786):
        return None

    if pc_end.price <= pa_end.price:
        return None
    wave_c = pc_end.price - pb_end.price
    c_ext = float(wave_c / wave_a)
    if not (0.786 <= c_ext <= 1.618):
        return None

    if bars[-1].close <= pb_end.price:
        return None

    return ElliottWavePattern(
        setup_type="ABC_REVERSAL",
        direction="SELL",
        pivots=[pa_start, pa_end, pb_end, pc_end],
        wave_1_size=wave_a,
        retracement_ratio=round(b_retrace, 3),
        extension_ratio=round(c_ext, 3),
        invalidation_level=pc_end.price,
        fib_target_price=pa_start.price,
        summary=(
            f"Bearish ABC correction complete: B={b_retrace * 100:.1f}% of A, "
            f"C={c_ext:.2f}x A reversing into H1 downtrend"
        ),
    )
