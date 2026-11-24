"""SMC/ICT, CRT, Candlestick, Volume & Significant Swing (IDM/POI/MSS) detectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.mt5.market_data import CandleBar

Direction = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class PoiZone:
    kind: Literal["OB", "FVG", "BREAKER", "EQH_EQL", "SUPPLY_DEMAND"]
    direction: Direction
    low: Decimal
    high: Decimal
    bar_index: int


@dataclass(frozen=True)
class SignificantSwing:
    index: int
    candle_time: datetime
    price: Decimal
    kind: Literal["HIGH", "LOW"]
    swept_idm: bool = False
    confirmed_bos: bool = False
    high_volume_break: bool = False


@dataclass(frozen=True)
class SmcConfluenceReport:
    liquidity_sweep: bool
    shakeout: bool
    crt_confirmed: bool
    htf_hammer_or_pinbar: bool
    mss_confirmed: bool
    displacement: bool
    fvg_created: bool
    follow_through: bool
    volume_contraction_pullback: bool
    volume_expansion_trigger: bool
    vsa_exhaustion: bool
    tapped_pois: list[str]
    score: int
    reasons: list[str]


def body_ratio(bar: CandleBar) -> float:
    span = float(bar.high - bar.low)
    if span <= 0:
        return 0.0
    return abs(float(bar.close - bar.open)) / span


def detect_hammer_or_pinbar(bar: CandleBar, direction: Direction) -> bool:
    """Detect Bullish Hammer / Pin Bar (BUY) or Bearish Shooting Star / Pin Bar (SELL)."""
    span = float(bar.high - bar.low)
    if span <= 0:
        return False
    body = abs(float(bar.close - bar.open))
    upper_wick = float(bar.high - max(bar.open, bar.close))
    lower_wick = float(min(bar.open, bar.close) - bar.low)

    if direction == "BUY":
        return (
            lower_wick >= max(body * 1.8, span * 0.50)
            and upper_wick <= span * 0.25
            and float(bar.close) >= float(bar.low) + span * 0.60
        )
    return (
        upper_wick >= max(body * 1.8, span * 0.50)
        and lower_wick <= span * 0.25
        and float(bar.close) <= float(bar.high) - span * 0.60
    )


def detect_crt(bars: list[CandleBar], direction: Direction, lookback: int = 4) -> bool:
    """Candle Range Theory (CRT): sweep outside prior candle range and close back inside."""
    if len(bars) < 3:
        return False
    start = max(1, len(bars) - lookback)
    for i in range(start, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]
        if direction == "BUY":
            # CRT: sweep below prev low & close back inside prev range (manipulation!)
            if curr.low < prev.low and curr.close > prev.low:
                return True
        else:
            # CRT: sweep above prev high & close back inside prev range (manipulation!)
            if curr.high > prev.high and curr.close < prev.high:
                return True
    return False


def detect_fvg_zones(
    bars: list[CandleBar], direction: Direction, lookback: int = 35
) -> list[PoiZone]:
    """Detect 3-candle Fair Value Gaps (FVG / Imbalance)."""
    if len(bars) < 3:
        return []
    zones: list[PoiZone] = []
    start = max(2, len(bars) - lookback)
    for i in range(start, len(bars)):
        b0 = bars[i - 2]
        b2 = bars[i]
        if direction == "BUY" and b0.high < b2.low:
            zones.append(
                PoiZone(
                    kind="FVG",
                    direction="BUY",
                    low=b0.high,
                    high=b2.low,
                    bar_index=i - 1,
                )
            )
        elif direction == "SELL" and b0.low > b2.high:
            zones.append(
                PoiZone(
                    kind="FVG",
                    direction="SELL",
                    low=b2.high,
                    high=b0.low,
                    bar_index=i - 1,
                )
            )
    return zones


def detect_order_blocks(
    bars: list[CandleBar],
    direction: Direction,
    atr: Decimal,
    lookback: int = 40,
) -> list[PoiZone]:
    """Detect institutional Order Blocks (last opposite candle before impulsive displacement)."""
    if len(bars) < 4 or atr <= 0:
        return []
    zones: list[PoiZone] = []
    start = max(0, len(bars) - lookback)
    for i in range(start, len(bars) - 1):
        ob = bars[i]
        imp = bars[i + 1]
        imp_body = abs(imp.close - imp.open)
        if imp_body < atr * Decimal("0.9"):
            continue
        if direction == "BUY":
            if ob.close < ob.open and imp.close > imp.open and imp.close > ob.high:
                zones.append(
                    PoiZone(
                        kind="OB",
                        direction="BUY",
                        low=ob.low,
                        high=ob.high,
                        bar_index=i,
                    )
                )
        else:
            if ob.close > ob.open and imp.close < imp.open and imp.close < ob.low:
                zones.append(
                    PoiZone(
                        kind="OB",
                        direction="SELL",
                        low=ob.low,
                        high=ob.high,
                        bar_index=i,
                    )
                )
    return zones


def detect_breaker_blocks(
    bars: list[CandleBar],
    direction: Direction,
    atr: Decimal,
    lookback: int = 45,
) -> list[PoiZone]:
    """Detect Breaker / Mitigation Blocks (failed opposite OB broken and flipped polarity)."""
    opp: Direction = "SELL" if direction == "BUY" else "BUY"
    opp_obs = detect_order_blocks(bars, opp, atr, lookback=lookback)
    breakers: list[PoiZone] = []
    for ob in opp_obs:
        # Check if a later candle strongly broke through the opposite OB
        for j in range(ob.bar_index + 2, len(bars) - 1):
            if direction == "BUY" and bars[j].close > ob.high:
                breakers.append(
                    PoiZone(
                        kind="BREAKER",
                        direction="BUY",
                        low=ob.low,
                        high=ob.high,
                        bar_index=ob.bar_index,
                    )
                )
                break
            if direction == "SELL" and bars[j].close < ob.low:
                breakers.append(
                    PoiZone(
                        kind="BREAKER",
                        direction="SELL",
                        low=ob.low,
                        high=ob.high,
                        bar_index=ob.bar_index,
                    )
                )
                break
    return breakers


def detect_liquidity_sweep_and_shakeout(
    bars: list[CandleBar],
    direction: Direction,
    atr: Decimal,
    *,
    sweep_window: int = 5,
    prior_lookback: int = 30,
) -> tuple[bool, bool, bool]:
    """Return (liquidity_sweep, shakeout_false_break, eqh_eql_swept)."""
    if len(bars) < sweep_window + 5 or atr <= 0:
        return False, False, False

    recent = bars[-sweep_window:]
    prior = bars[max(0, len(bars) - sweep_window - prior_lookback) : -sweep_window]
    if not prior:
        return False, False, False

    eq_tol = atr * Decimal("0.20")
    last_close = bars[-1].close

    if direction == "BUY":
        prior_lows = [b.low for b in prior]
        key_low = min(prior_lows)
        # Check Equal Lows (EQL) cluster
        eql_count = sum(1 for lv in prior_lows if abs(lv - key_low) <= eq_tol)
        swept_below = any(b.low < key_low for b in recent)
        recovered = last_close > key_low
        # Shakeout: dipped below prior low & closed decisively back above it
        shakeout = swept_below and recovered and bars[-1].close > bars[-1].open
        eq_swept = swept_below and recovered and eql_count >= 2
        return (swept_below and recovered), shakeout, eq_swept

    prior_highs = [b.high for b in prior]
    key_high = max(prior_highs)
    eqh_count = sum(1 for hv in prior_highs if abs(hv - key_high) <= eq_tol)
    swept_above = any(b.high > key_high for b in recent)
    recovered = last_close < key_high
    shakeout = swept_above and recovered and bars[-1].close < bars[-1].open
    eq_swept = swept_above and recovered and eqh_count >= 2
    return (swept_above and recovered), shakeout, eq_swept


def detect_mss_and_displacement(
    bars: list[CandleBar],
    direction: Direction,
    atr: Decimal,
    *,
    swing_window: int = 8,
) -> tuple[bool, bool, bool, bool]:
    """Check L-S-M-D-F: (mss_confirmed, displacement, fvg_created, follow_through)."""
    if len(bars) < swing_window + 3 or atr <= 0:
        return False, False, False, False

    last = bars[-1]
    prev = bars[-2]
    prev2 = bars[-3]
    recent_struct = bars[-(swing_window + 1) : -1]

    # Internal 3-candle swing high/low inside recent corrective structure
    if direction == "BUY":
        swing_highs = [
            recent_struct[i].high
            for i in range(1, len(recent_struct) - 1)
            if recent_struct[i].high >= recent_struct[i - 1].high
            and recent_struct[i].high >= recent_struct[i + 1].high
        ]
        ref_high = swing_highs[-1] if swing_highs else max(b.high for b in bars[-4:-1])
        mss = (
            last.close > ref_high
            or (last.close > prev.high and last.close > last.open)
        )

        disp_body = max(
            float(last.close - last.open),
            float(prev.close - prev.open),
        )
        displacement = (
            last.close > last.open
            and body_ratio(last) >= 0.45
            and Decimal(str(max(0.0, disp_body))) >= atr * Decimal("0.55")
        )
        fvg_created = (prev2.high < last.low) or (
            len(bars) >= 4 and bars[-4].high < prev.low
        )
        follow_through = (
            last.close > prev.close
            and last.low >= min(prev.low, prev2.low)
        )
        return mss, displacement, fvg_created, follow_through

    swing_lows = [
        recent_struct[i].low
        for i in range(1, len(recent_struct) - 1)
        if recent_struct[i].low <= recent_struct[i - 1].low
        and recent_struct[i].low <= recent_struct[i + 1].low
    ]
    ref_low = swing_lows[-1] if swing_lows else min(b.low for b in bars[-4:-1])
    mss = (
        last.close < ref_low
        or (last.close < prev.low and last.close < last.open)
    )

    disp_body = max(
        float(last.open - last.close),
        float(prev.open - prev.close),
    )
    displacement = (
        last.close < last.open
        and body_ratio(last) >= 0.45
        and Decimal(str(max(0.0, disp_body))) >= atr * Decimal("0.55")
    )
    fvg_created = (prev2.low > last.high) or (
        len(bars) >= 4 and bars[-4].low > prev.high
    )
    follow_through = (
        last.close < prev.close
        and last.high <= max(prev.high, prev2.high)
    )
    return mss, displacement, fvg_created, follow_through


def detect_volume_behavior(
    bars: list[CandleBar], *, pullback_bars: int = 5
) -> tuple[bool, bool, bool]:
    """Return (volume_contraction_pullback, volume_expansion_trigger, vsa_exhaustion)."""
    if len(bars) < 22:
        return False, False, False

    vols = [float(b.volume) for b in bars[-22:]]
    avg_vol = sum(vols[:-1]) / max(1, len(vols) - 1)
    if avg_vol <= 0:
        return False, False, False

    trigger_vol = vols[-1]
    prev_vol = vols[-2]
    volume_expansion = max(trigger_vol, prev_vol) >= avg_vol * 1.15

    # Pullback contraction: earlier half of pullback had higher vol than later half
    pb = vols[-(pullback_bars + 1) : -1]
    if len(pb) >= 4:
        half = len(pb) // 2
        vol_contraction = (sum(pb[half:]) / len(pb[half:])) <= (
            sum(pb[:half]) / len(pb[:half])
        ) * 1.05
    else:
        vol_contraction = False

    # VSA Effort vs Result exhaustion: high volume with small body progress
    last = bars[-1]
    vsa_exhaustion = (
        trigger_vol >= avg_vol * 1.4 and body_ratio(last) <= 0.30
    ) or (
        len(bars) >= 4
        and vols[-1] < vols[-2] < vols[-3]
    )
    return vol_contraction, volume_expansion, vsa_exhaustion


def detect_significant_swings_idm(
    bars: list[CandleBar],
    *,
    vol_expansion_mult: float = 1.2,
) -> list[SignificantSwing]:
    """Identify Significant Swings using Inducement (IDM) -> Major High/Low -> BOS/CHoCH.

    Small internal pullbacks remain Noise (Single Move) until a prior internal swing
    (IDM) is swept. Once IDM is swept, the Major High/Low is marked; breaking it
    confirms a valid BOS/CHoCH wave (with dynamic IDM shift if BOS fails).
    """
    if len(bars) < 10:
        return []

    vols = [float(b.volume) for b in bars]
    swings: list[SignificantSwing] = []

    # Causal 3-bar fractal internal swings
    internal_highs: list[tuple[int, Decimal]] = []
    internal_lows: list[tuple[int, Decimal]] = []

    major_high: tuple[int, Decimal] | None = None
    major_low: tuple[int, Decimal] | None = None
    idm_low: tuple[int, Decimal] | None = None
    idm_high: tuple[int, Decimal] | None = None
    idm_low_swept = False
    idm_high_swept = False

    for i in range(2, len(bars)):
        prev2, prev1, curr = bars[i - 2], bars[i - 1], bars[i]
        avg_v = sum(vols[max(0, i - 20) : i]) / max(1, min(20, i))
        hi_vol = avg_v > 0 and vols[i] >= avg_v * vol_expansion_mult

        # Confirmed internal fractal at i-1 (strictly causal at bar i)
        if prev1.high >= prev2.high and prev1.high >= curr.high:
            internal_highs.append((i - 1, prev1.high))
            if major_high is None or prev1.high > major_high[1]:
                major_high = (i - 1, prev1.high)
                # Set IDM to latest internal low before this high
                if internal_lows:
                    idm_low = internal_lows[-1]
                    idm_low_swept = False

        if prev1.low <= prev2.low and prev1.low <= curr.low:
            internal_lows.append((i - 1, prev1.low))
            if major_low is None or prev1.low < major_low[1]:
                major_low = (i - 1, prev1.low)
                if internal_highs:
                    idm_high = internal_highs[-1]
                    idm_high_swept = False

        # Bullish IDM sweep (sweeps internal low -> marks Major High valid!)
        if idm_low is not None and curr.low < idm_low[1]:
            if not idm_low_swept and major_high is not None:
                idm_low_swept = True
                swings.append(
                    SignificantSwing(
                        index=major_high[0],
                        candle_time=bars[major_high[0]].candle_time,
                        price=major_high[1],
                        kind="HIGH",
                        swept_idm=True,
                        confirmed_bos=False,
                        high_volume_break=hi_vol,
                    )
                )
            else:
                # Dynamic IDM shift lower if BOS failed after prior sweep
                idm_low = (i, curr.low)

        # Bullish BOS/CHoCH above marked Major High after IDM sweep
        if idm_low_swept and major_high is not None and curr.close > major_high[1]:
            # Mark the pullback low between major_high and i as Significant LOW
            low_idx = min(
                range(major_high[0], i + 1), key=lambda k: bars[k].low
            )
            swings.append(
                SignificantSwing(
                    index=low_idx,
                    candle_time=bars[low_idx].candle_time,
                    price=bars[low_idx].low,
                    kind="LOW",
                    swept_idm=True,
                    confirmed_bos=True,
                    high_volume_break=hi_vol,
                )
            )
            major_high = (i, curr.high)
            idm_low = (low_idx, bars[low_idx].low)
            idm_low_swept = False

        # Bearish IDM sweep (sweeps internal high -> marks Major Low valid!)
        if idm_high is not None and curr.high > idm_high[1]:
            if not idm_high_swept and major_low is not None:
                idm_high_swept = True
                swings.append(
                    SignificantSwing(
                        index=major_low[0],
                        candle_time=bars[major_low[0]].candle_time,
                        price=major_low[1],
                        kind="LOW",
                        swept_idm=True,
                        confirmed_bos=False,
                        high_volume_break=hi_vol,
                    )
                )
            else:
                idm_high = (i, curr.high)

        # Bearish BOS/CHoCH below marked Major Low after IDM sweep
        if idm_high_swept and major_low is not None and curr.close < major_low[1]:
            high_idx = max(
                range(major_low[0], i + 1), key=lambda k: bars[k].high
            )
            swings.append(
                SignificantSwing(
                    index=high_idx,
                    candle_time=bars[high_idx].candle_time,
                    price=bars[high_idx].high,
                    kind="HIGH",
                    swept_idm=True,
                    confirmed_bos=True,
                    high_volume_break=hi_vol,
                )
            )
            major_low = (i, curr.low)
            idm_high = (high_idx, bars[high_idx].high)
            idm_high_swept = False

    return swings


def evaluate_smc_confluence(
    *,
    m15_bars: list[CandleBar],
    h1_bars: list[CandleBar],
    direction: Direction,
    atr: Decimal,
    pullback_extreme: Decimal,
) -> SmcConfluenceReport:
    """Evaluate all SMC/ICT, CRT, POI, L-S-M-D-F, and Volume confirmations."""
    liq_sweep, shakeout, eq_swept = detect_liquidity_sweep_and_shakeout(
        m15_bars, direction, atr
    )
    h1_sweep, h1_shakeout, _ = detect_liquidity_sweep_and_shakeout(
        h1_bars, direction, atr * Decimal("2.0"), sweep_window=3, prior_lookback=20
    )
    liq_sweep = liq_sweep or h1_sweep
    shakeout = shakeout or h1_shakeout

    crt_m15 = detect_crt(m15_bars, direction, lookback=4)
    crt_h1 = detect_crt(h1_bars, direction, lookback=3)
    crt_confirmed = crt_m15 or crt_h1

    htf_pin = any(
        detect_hammer_or_pinbar(b, direction) for b in h1_bars[-3:]
    ) or detect_hammer_or_pinbar(m15_bars[-1], direction)

    mss, disp, fvg_created, follow_through = detect_mss_and_displacement(
        m15_bars, direction, atr
    )
    vol_contract, vol_expand, vsa_exhaust = detect_volume_behavior(m15_bars)

    # Check if pullback extreme tapped any POI (OB, FVG, Breaker, EQH/EQL)
    tol = atr * Decimal("0.30")
    tapped_pois: list[str] = []
    if eq_swept:
        tapped_pois.append("EQL" if direction == "BUY" else "EQH")

    for zone in detect_order_blocks(m15_bars[:-1], direction, atr):
        if zone.low - tol <= pullback_extreme <= zone.high + tol:
            tapped_pois.append("OB")
            break
    for zone in detect_fvg_zones(m15_bars[:-1], direction):
        if zone.low - tol <= pullback_extreme <= zone.high + tol:
            tapped_pois.append("FVG")
            break
    for zone in detect_breaker_blocks(m15_bars[:-1], direction, atr):
        if zone.low - tol <= pullback_extreme <= zone.high + tol:
            tapped_pois.append("BREAKER")
            break

    reasons: list[str] = []
    score = 0
    if liq_sweep or shakeout:
        score += 1
        reasons.append("Liquidity Sweep / Shakeout confirmed")
    if mss:
        score += 1
        reasons.append("Bullish/Bearish MSS (CHoCH) confirmed")
    if disp or fvg_created:
        score += 1
        reasons.append("Displacement / FVG impulse confirmed")
    if vol_expand or vol_contract:
        score += 1
        reasons.append("Volume shift (pullback contraction / trigger expansion)")
    if crt_confirmed:
        score += 1
        reasons.append("CRT (Candle Range Theory) manipulation confirmed")
    if htf_pin:
        score += 1
        reasons.append("Hammer / Pin Bar rejection confirmed")
    if tapped_pois:
        score += 1
        reasons.append(f"POI confluence tapped ({', '.join(tapped_pois)})")
    if follow_through:
        score += 1

    return SmcConfluenceReport(
        liquidity_sweep=liq_sweep,
        shakeout=shakeout,
        crt_confirmed=crt_confirmed,
        htf_hammer_or_pinbar=htf_pin,
        mss_confirmed=mss,
        displacement=disp,
        fvg_created=fvg_created,
        follow_through=follow_through,
        volume_contraction_pullback=vol_contract,
        volume_expansion_trigger=vol_expand,
        vsa_exhaustion=vsa_exhaust,
        tapped_pois=tapped_pois,
        score=score,
        reasons=reasons,
    )
