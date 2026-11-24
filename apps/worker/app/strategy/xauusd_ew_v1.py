"""Deterministic Elliott Wave v1 strategy for XAUUSD (H1 trend + M15 Wave 3 / Wave 5 / ABC)."""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from app.market.elliott_wave import (
    ElliottWavePattern,
    detect_abc_reversal,
    detect_causal_zigzag,
    detect_wave_3,
    detect_wave_5,
)
from app.market.features import (
    active_blackout_at,
    is_in_allowed_session,
    is_in_rollover_break,
    is_weekend_market_closed,
)
from app.market.indicators import latest_indicators
from app.mt5.market_data import CandleBar
from app.strategy.base import BaseStrategy
from app.strategy.signal import SetupCandidate, StrategyContext, build_dedup_key


def _round_price(value: Decimal, digits: int = 3) -> Decimal:
    quant = Decimal("1").scaleb(-digits)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _body_ratio(bar: CandleBar) -> float:
    full_range = float(bar.high - bar.low)
    if full_range <= 0:
        return 0.0
    return abs(float(bar.close - bar.open)) / full_range


class XauusdElliottWaveV1Strategy(BaseStrategy):
    """H1 macro trend + M15 causal Elliott Wave (Wave 3, Wave 5, ABC Correction)."""

    def __init__(
        self,
        *,
        strategy_id: str = "xauusd_ew_v1",
        version: str = "1.0.0",
        ema_fast: int = 20,
        ema_mid: int = 50,
        ema_slow: int = 200,
        rsi_period: int = 14,
        atr_period: int = 14,
        zigzag_atr_mult: float = 1.5,
        sl_atr_buffer_multiplier: float = 0.75,
        min_sl_atr_multiplier: float = 1.3,
        max_sl_atr_multiplier: float = 4.5,
        target_reward_risk: float = 2.0,
        max_reward_risk: float = 2.5,
        min_body_ratio: float = 0.35,
        h1_min_ema_sep_atr: float = 0.20,
        enable_wave_3: bool = True,
        enable_wave_5: bool = True,
        enable_abc: bool = True,
        price_digits: int = 3,
    ) -> None:
        self.strategy_id = strategy_id
        self.name = "xauusd_ew_v1"
        self.version = version
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.zigzag_atr_mult = Decimal(str(zigzag_atr_mult))
        self.sl_atr_buffer_multiplier = Decimal(str(sl_atr_buffer_multiplier))
        self.min_sl_atr_multiplier = Decimal(str(min_sl_atr_multiplier))
        self.max_sl_atr_multiplier = Decimal(str(max_sl_atr_multiplier))
        self.target_reward_risk = Decimal(str(target_reward_risk))
        self.max_reward_risk = Decimal(str(max_reward_risk))
        self.min_body_ratio = min_body_ratio
        self.h1_min_ema_sep_atr = h1_min_ema_sep_atr
        self.enable_wave_3 = enable_wave_3
        self.enable_wave_5 = enable_wave_5
        self.enable_abc = enable_abc
        self.price_digits = price_digits

    def _detect_best_pattern(
        self,
        *,
        m15_ordered: list[CandleBar],
        direction: Literal["BUY", "SELL"],
        atr_dec: Decimal,
    ) -> ElliottWavePattern | None:
        pivots = detect_causal_zigzag(
            m15_ordered,
            atr_value=atr_dec,
            reversal_atr_mult=self.zigzag_atr_mult,
        )
        if len(pivots) < 2:
            return None

        # Priority: Wave 5 (most specific 5-pivot structure) -> Wave 3 -> ABC reversal
        if self.enable_wave_5:
            p5 = detect_wave_5(
                m15_ordered, pivots, direction=direction, atr_value=atr_dec
            )
            if p5 is not None:
                return p5

        if self.enable_wave_3:
            p3 = detect_wave_3(
                m15_ordered, pivots, direction=direction, atr_value=atr_dec
            )
            if p3 is not None:
                return p3

        if self.enable_abc:
            p_abc = detect_abc_reversal(
                m15_ordered, pivots, direction=direction, atr_value=atr_dec
            )
            if p_abc is not None:
                return p_abc

        return None

    def evaluate(self, ctx: StrategyContext) -> SetupCandidate | None:
        # 1. Hard environment & session filters
        if ctx.current_bid <= 0 or ctx.current_ask <= 0 or ctx.current_spread < 0:
            return None
        if ctx.current_spread > ctx.max_spread:
            return None
        if is_weekend_market_closed(ctx.now_utc):
            return None
        if is_in_rollover_break(
            ctx.now_utc,
            rollover_start_utc=ctx.rollover_start_utc,
            rollover_end_utc=ctx.rollover_end_utc,
        ):
            return None
        if not is_in_allowed_session(ctx.now_utc, ctx.allowed_sessions):
            return None
        if active_blackout_at(ctx.now_utc, ctx.blackouts) is not None:
            return None
        if ctx.cooldown_until is not None:
            now_dt = (
                ctx.now_utc.astimezone(UTC)
                if ctx.now_utc.tzinfo
                else ctx.now_utc.replace(tzinfo=UTC)
            )
            cd_dt = (
                ctx.cooldown_until.astimezone(UTC)
                if ctx.cooldown_until.tzinfo
                else ctx.cooldown_until.replace(tzinfo=UTC)
            )
            if now_dt < cd_dt:
                return None

        # 2. Require sufficient closed candles
        if len(ctx.h1_bars) < self.ema_mid or len(ctx.m15_bars) < max(self.ema_mid, 60):
            return None

        h1_ind = latest_indicators(
            ctx.h1_bars,
            ema_fast=self.ema_fast,
            ema_mid=self.ema_mid,
            ema_slow=self.ema_slow,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
        )
        m15_ind = latest_indicators(
            ctx.m15_bars,
            ema_fast=self.ema_fast,
            ema_mid=self.ema_mid,
            ema_slow=self.ema_slow,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
        )
        if h1_ind is None or m15_ind is None or m15_ind.atr_14 <= 0 or h1_ind.atr_14 <= 0:
            return None

        h1_ordered = sorted(ctx.h1_bars, key=lambda b: b.candle_time)
        m15_ordered = sorted(ctx.m15_bars, key=lambda b: b.candle_time)
        h1_last = h1_ordered[-1]
        m15_last = m15_ordered[-1]
        m15_prev = m15_ordered[-2]

        # 3. H1 Macro Trend + Chop Filter (EMA20/EMA50 separation vs H1 ATR)
        h1_close_f = float(h1_last.close)
        ema_sep = abs(h1_ind.ema_20 - h1_ind.ema_50)
        trend_strong_enough = ema_sep >= (h1_ind.atr_14 * self.h1_min_ema_sep_atr)
        if not trend_strong_enough:
            return None

        h1_bullish = (
            h1_close_f > h1_ind.ema_50
            and h1_ind.ema_20 > h1_ind.ema_50
            and (h1_ind.ema_200 is None or h1_ind.ema_50 >= h1_ind.ema_200)
        )
        h1_bearish = (
            h1_close_f < h1_ind.ema_50
            and h1_ind.ema_20 < h1_ind.ema_50
            and (h1_ind.ema_200 is None or h1_ind.ema_50 <= h1_ind.ema_200)
        )

        direction: Literal["BUY", "SELL"] | None = None
        if h1_bullish:
            direction = "BUY"
        elif h1_bearish:
            direction = "SELL"
        else:
            return None

        # 4. Closed M15 reversal/continuation candle confirmation
        body_ratio = _body_ratio(m15_last)
        if body_ratio < self.min_body_ratio:
            return None

        ema20_dec = Decimal(str(m15_ind.ema_20))
        if direction == "BUY":
            candle_ok = (
                m15_last.close > m15_last.open
                and m15_last.close > ema20_dec
                and m15_last.close > m15_prev.close
                and 42.0 <= m15_ind.rsi_14 <= 70.0
            )
        else:
            candle_ok = (
                m15_last.close < m15_last.open
                and m15_last.close < ema20_dec
                and m15_last.close < m15_prev.close
                and 30.0 <= m15_ind.rsi_14 <= 58.0
            )
        if not candle_ok:
            return None

        # 5. Detect valid causal Elliott Wave pattern
        atr_dec = Decimal(str(m15_ind.atr_14))
        pattern = self._detect_best_pattern(
            m15_ordered=m15_ordered[-120:],
            direction=direction,
            atr_dec=atr_dec,
        )
        if pattern is None:
            return None

        dedup_key = build_dedup_key(
            strategy_id=self.strategy_id,
            symbol=ctx.symbol,
            timeframe="M15",
            candle_time=m15_last.candle_time,
            direction=direction,
        )
        if dedup_key in ctx.existing_dedup_keys:
            return None

        # 6. Compute SL from Elliott Wave invalidation level + ATR protection
        sl_buffer = atr_dec * self.sl_atr_buffer_multiplier
        min_sl_dist = atr_dec * self.min_sl_atr_multiplier
        max_sl_dist = atr_dec * self.max_sl_atr_multiplier

        if direction == "BUY":
            entry = _round_price(ctx.current_ask, self.price_digits)
            raw_sl = pattern.invalidation_level - sl_buffer
            risk_distance = entry - raw_sl
            if risk_distance < min_sl_dist:
                raw_sl = entry - min_sl_dist
                risk_distance = min_sl_dist
            if risk_distance <= 0 or risk_distance > max_sl_dist:
                return None
            stop_loss = _round_price(raw_sl, self.price_digits)
            risk_distance = entry - stop_loss

            min_tp = entry + (risk_distance * self.target_reward_risk)
            max_tp = entry + (risk_distance * self.max_reward_risk)
            desired_tp = max(min_tp, min(pattern.fib_target_price, max_tp))
            take_profit = _round_price(desired_tp, self.price_digits)
            actual_rr = (take_profit - entry) / risk_distance
        else:
            entry = _round_price(ctx.current_bid, self.price_digits)
            raw_sl = pattern.invalidation_level + sl_buffer
            risk_distance = raw_sl - entry
            if risk_distance < min_sl_dist:
                raw_sl = entry + min_sl_dist
                risk_distance = min_sl_dist
            if risk_distance <= 0 or risk_distance > max_sl_dist:
                return None
            stop_loss = _round_price(raw_sl, self.price_digits)
            risk_distance = stop_loss - entry

            min_tp = entry - (risk_distance * self.target_reward_risk)
            max_tp = entry - (risk_distance * self.max_reward_risk)
            desired_tp = min(min_tp, max(pattern.fib_target_price, max_tp))
            take_profit = _round_price(desired_tp, self.price_digits)
            actual_rr = (entry - take_profit) / risk_distance

        if actual_rr < self.target_reward_risk:
            return None

        indicators_payload: dict[str, Any] = {
            "h1": h1_ind.model_dump(),
            "m15": m15_ind.model_dump(),
            "bodyRatio": round(body_ratio, 3),
            "elliottWave": {
                "setupType": pattern.setup_type,
                "retracementRatio": pattern.retracement_ratio,
                "extensionRatio": pattern.extension_ratio,
                "invalidationLevel": str(pattern.invalidation_level),
                "fibTargetPrice": str(_round_price(pattern.fib_target_price, self.price_digits)),
                "pivots": [
                    {
                        "kind": p.kind,
                        "price": str(p.price),
                        "time": p.candle_time.isoformat(),
                    }
                    for p in pattern.pivots
                ],
            },
        }

        return SetupCandidate(
            strategy_id=self.strategy_id,
            strategy_name=self.name,
            strategy_version=self.version,
            symbol=ctx.symbol,
            timeframe="M15",
            direction=direction,
            candle_time=m15_last.candle_time,
            entry_reference=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reward_risk=actual_rr.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            dedup_key=dedup_key,
            deterministic_reasons=[
                f"H1 trend {direction} aligned (EMA20/EMA50 separation ok, no chop)",
                pattern.summary,
                f"Closed M15 confirmation candle (body={body_ratio:.2f}, RSI={m15_ind.rsi_14:.1f})",
            ],
            invalidation_rules=[
                (
                    f"Elliott Wave {pattern.setup_type} invalidated if price "
                    f"breaches {pattern.invalidation_level}"
                ),
                "H1 close beyond EMA50 invalidates higher-timeframe wave bias",
            ],
            indicators=indicators_payload,
        )
