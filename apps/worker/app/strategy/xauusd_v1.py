"""Deterministic XAUUSD v1 rule-based strategy (H1 trend + M15 continuation)."""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.market.features import (
    active_blackout_at,
    extract_market_structure,
    is_in_allowed_session,
    is_in_rollover_break,
    is_weekend_market_closed,
)
from app.market.indicators import latest_indicators
from app.strategy.base import BaseStrategy
from app.strategy.signal import SetupCandidate, StrategyContext, build_dedup_key


def _round_price(value: Decimal, digits: int = 2) -> Decimal:
    quant = Decimal("1").scaleb(-digits)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


class XauusdV1Strategy(BaseStrategy):
    """Transparent H1 trend + M15 pullback continuation strategy (MARKET orders only)."""

    def __init__(
        self,
        *,
        strategy_id: str = "xauusd_v1",
        version: str = "1.0.0",
        ema_fast: int = 20,
        ema_mid: int = 50,
        ema_slow: int = 200,
        rsi_period: int = 14,
        atr_period: int = 14,
        sl_atr_buffer_multiplier: float = 0.5,
        target_reward_risk: float = 2.0,
        swing_lookback: int = 8,
        price_digits: int = 2,
    ) -> None:
        self.strategy_id = strategy_id
        self.name = "xauusd_v1"
        self.version = version
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.sl_atr_buffer_multiplier = Decimal(str(sl_atr_buffer_multiplier))
        self.target_reward_risk = Decimal(str(target_reward_risk))
        self.swing_lookback = swing_lookback
        self.price_digits = price_digits

    def evaluate(self, ctx: StrategyContext) -> SetupCandidate | None:
        # 1. Hard environment/market-state filters
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

        # 2. Require sufficient closed candles for H1 and M15
        if len(ctx.h1_bars) < self.ema_mid or len(ctx.m15_bars) < max(
            self.ema_mid, self.swing_lookback + 2
        ):
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
        if h1_ind is None or m15_ind is None or m15_ind.atr_14 <= 0:
            return None

        h1_ordered = sorted(ctx.h1_bars, key=lambda b: b.candle_time)
        m15_ordered = sorted(ctx.m15_bars, key=lambda b: b.candle_time)
        h1_last = h1_ordered[-1]
        m15_last = m15_ordered[-1]
        m15_prev = m15_ordered[-2]

        structure = extract_market_structure(m15_ordered, lookback=self.swing_lookback)
        if structure is None:
            return None

        # 3. H1 Trend Filter
        h1_close_f = float(h1_last.close)
        h1_bullish = h1_close_f > h1_ind.ema_20 > h1_ind.ema_50 and (
            h1_ind.ema_200 is None or h1_ind.ema_50 >= h1_ind.ema_200
        )
        h1_bearish = h1_close_f < h1_ind.ema_20 < h1_ind.ema_50 and (
            h1_ind.ema_200 is None or h1_ind.ema_50 <= h1_ind.ema_200
        )

        ema20_dec = Decimal(str(m15_ind.ema_20))
        ema50_dec = Decimal(str(m15_ind.ema_50))
        atr_dec = Decimal(str(m15_ind.atr_14))
        sl_buffer = atr_dec * self.sl_atr_buffer_multiplier

        indicators_payload: dict[str, Any] = {
            "h1": h1_ind.model_dump(),
            "m15": m15_ind.model_dump(),
            "swingHigh": str(structure.recent_swing_high),
            "swingLow": str(structure.recent_swing_low),
            "bodyRatio": structure.last_body_ratio,
        }

        # 4. Long candidate: H1 bullish + M15 pullback near EMA20 + bullish close confirmation
        if h1_bullish:
            pulled_back = (
                m15_prev.low <= ema20_dec + (atr_dec * Decimal("0.25"))
                or m15_last.low <= ema20_dec + (atr_dec * Decimal("0.25"))
            )
            bullish_trigger = (
                m15_last.close > m15_last.open
                and m15_last.close > ema20_dec
                and m15_last.close > ema50_dec
                and 45.0 <= m15_ind.rsi_14 <= 72.0
            )
            if pulled_back and bullish_trigger:
                dedup_key = build_dedup_key(
                    strategy_id=self.strategy_id,
                    symbol=ctx.symbol,
                    timeframe="M15",
                    candle_time=m15_last.candle_time,
                    direction="BUY",
                )
                if dedup_key in ctx.existing_dedup_keys:
                    return None

                entry = _round_price(ctx.current_ask, self.price_digits)
                stop_loss = _round_price(
                    structure.recent_swing_low - sl_buffer, self.price_digits
                )
                risk_distance = entry - stop_loss
                if risk_distance <= 0:
                    return None

                take_profit = _round_price(
                    entry + (risk_distance * self.target_reward_risk),
                    self.price_digits,
                )
                return SetupCandidate(
                    strategy_id=self.strategy_id,
                    strategy_name=self.name,
                    strategy_version=self.version,
                    symbol=ctx.symbol,
                    timeframe="M15",
                    direction="BUY",
                    candle_time=m15_last.candle_time,
                    entry_reference=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reward_risk=self.target_reward_risk,
                    dedup_key=dedup_key,
                    deterministic_reasons=[
                        "H1 trend filter bullish (close > EMA20 > EMA50)",
                        "M15 pullback into EMA20 zone with closed bullish continuation candle",
                        f"M15 RSI(14)={m15_ind.rsi_14:.1f} confirms bullish momentum",
                    ],
                    invalidation_rules=[
                        (
                            f"Close or break below M15 swing low "
                            f"({structure.recent_swing_low}) invalidates setup"
                        ),
                        "H1 close below EMA50 invalidates higher-timeframe trend",
                    ],
                    indicators=indicators_payload,
                )

        # 5. Short candidate (mirror of long)
        if h1_bearish:
            pulled_back = (
                m15_prev.high >= ema20_dec - (atr_dec * Decimal("0.25"))
                or m15_last.high >= ema20_dec - (atr_dec * Decimal("0.25"))
            )
            bearish_trigger = (
                m15_last.close < m15_last.open
                and m15_last.close < ema20_dec
                and m15_last.close < ema50_dec
                and 28.0 <= m15_ind.rsi_14 <= 55.0
            )
            if pulled_back and bearish_trigger:
                dedup_key = build_dedup_key(
                    strategy_id=self.strategy_id,
                    symbol=ctx.symbol,
                    timeframe="M15",
                    candle_time=m15_last.candle_time,
                    direction="SELL",
                )
                if dedup_key in ctx.existing_dedup_keys:
                    return None

                entry = _round_price(ctx.current_bid, self.price_digits)
                stop_loss = _round_price(
                    structure.recent_swing_high + sl_buffer, self.price_digits
                )
                risk_distance = stop_loss - entry
                if risk_distance <= 0:
                    return None

                take_profit = _round_price(
                    entry - (risk_distance * self.target_reward_risk),
                    self.price_digits,
                )
                return SetupCandidate(
                    strategy_id=self.strategy_id,
                    strategy_name=self.name,
                    strategy_version=self.version,
                    symbol=ctx.symbol,
                    timeframe="M15",
                    direction="SELL",
                    candle_time=m15_last.candle_time,
                    entry_reference=entry,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    reward_risk=self.target_reward_risk,
                    dedup_key=dedup_key,
                    deterministic_reasons=[
                        "H1 trend filter bearish (close < EMA20 < EMA50)",
                        "M15 pullback into EMA20 zone with closed bearish continuation candle",
                        f"M15 RSI(14)={m15_ind.rsi_14:.1f} confirms bearish momentum",
                    ],
                    invalidation_rules=[
                        (
                            f"Close or break above M15 swing high "
                            f"({structure.recent_swing_high}) invalidates setup"
                        ),
                        "H1 close above EMA50 invalidates higher-timeframe trend",
                    ],
                    indicators=indicators_payload,
                )

        return None
