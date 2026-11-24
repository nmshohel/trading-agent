"""Elliott Wave + SMC/ICT + EMA 21/144 + MACD + VSA strategy (xauusd_ew_v1 v2.0.0)."""

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
from app.market.indicators import EwIndicatorSeries, compute_ew_series
from app.market.smc import (
    body_ratio,
    detect_significant_swings_idm,
    evaluate_smc_confluence,
)
from app.mt5.market_data import CandleBar
from app.strategy.base import BaseStrategy
from app.strategy.signal import SetupCandidate, StrategyContext, build_dedup_key


def _round_price(value: Decimal, digits: int = 3) -> Decimal:
    quant = Decimal("1").scaleb(-digits)
    return value.quantize(quant, rounding=ROUND_HALF_UP)


class XauusdElliottWaveV1Strategy(BaseStrategy):
    """Custom Elliott Wave + SMC/ICT + EMA 21/144 + MACD + L-S-M-D-F strategy."""

    def __init__(
        self,
        *,
        strategy_id: str = "xauusd_ew_v1",
        version: str = "2.0.0",
        ema_fast: int = 21,
        ema_slow: int = 144,
        rsi_period: int = 14,
        atr_period: int = 14,
        zigzag_atr_mult: float = 1.5,
        sl_atr_buffer_multiplier: float = 0.75,
        min_sl_atr_multiplier: float = 1.3,
        max_sl_atr_multiplier: float = 4.5,
        target_reward_risk: float = 2.0,
        max_reward_risk: float = 2.6,
        min_body_ratio: float = 0.35,
        min_smc_confluence: int = 2,
        h1_min_ema_sep_atr: float = 0.20,
        enable_wave_3: bool = True,
        enable_wave_5: bool = True,
        enable_abc: bool = True,
        price_digits: int = 3,
        # Accept legacy kwargs gracefully from DB parameters
        **_unused: Any,
    ) -> None:
        self.strategy_id = strategy_id
        self.name = "xauusd_ew_v1"
        self.version = version
        self.ema_fast = ema_fast
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
        self.min_smc_confluence = min_smc_confluence
        self.h1_min_ema_sep_atr = h1_min_ema_sep_atr
        self.enable_wave_3 = enable_wave_3
        self.enable_wave_5 = enable_wave_5
        self.enable_abc = enable_abc
        self.price_digits = price_digits

    def _detect_best_pattern(
        self,
        *,
        m15_window: list[CandleBar],
        m15_series: EwIndicatorSeries,
        h1_window: list[CandleBar],
        h1_series: EwIndicatorSeries,
        direction: Literal["BUY", "SELL"],
        atr_dec: Decimal,
    ) -> ElliottWavePattern | None:
        pivots = detect_causal_zigzag(
            m15_window,
            atr_value=atr_dec,
            reversal_atr_mult=self.zigzag_atr_mult,
        )
        if len(pivots) < 2:
            return None

        h1_atr = Decimal(str(max(h1_series.atr_14[-1], 0.01)))
        h1_pivots = detect_causal_zigzag(
            h1_window,
            atr_value=h1_atr,
            reversal_atr_mult=self.zigzag_atr_mult,
        )

        # Priority: Wave 5 (W3 MACD > W1) -> Wave 3 (81.2% + MACD Base) -> ABC (A-C div)
        if self.enable_wave_5:
            p5 = detect_wave_5(
                m15_window,
                pivots,
                direction=direction,
                atr_value=atr_dec,
                series=m15_series,
            )
            if p5 is not None:
                return p5

        if self.enable_wave_3:
            p3 = detect_wave_3(
                m15_window,
                pivots,
                direction=direction,
                atr_value=atr_dec,
                series=m15_series,
                h1_pivots=h1_pivots,
                h1_series=h1_series,
            )
            if p3 is not None:
                return p3

        if self.enable_abc:
            p_abc = detect_abc_reversal(
                m15_window,
                pivots,
                direction=direction,
                atr_value=atr_dec,
                series=m15_series,
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

        # 2. Compute EMA 21 / EMA 144 / MACD(12,26,9) / RSI / ATR series on H1 & M15
        h1_ordered = sorted(ctx.h1_bars, key=lambda b: b.candle_time)
        m15_ordered = sorted(ctx.m15_bars, key=lambda b: b.candle_time)
        if len(h1_ordered) < 50 or len(m15_ordered) < 60:
            return None

        h1_window = h1_ordered[-220:]
        m15_window = m15_ordered[-140:]

        h1_series = compute_ew_series(
            h1_window,
            ema_fast=self.ema_fast,
            ema_slow=self.ema_slow,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
        )
        m15_series = compute_ew_series(
            m15_window,
            ema_fast=self.ema_fast,
            ema_slow=self.ema_slow,
            rsi_period=self.rsi_period,
            atr_period=self.atr_period,
        )
        if h1_series is None or m15_series is None:
            return None

        h1_atr = h1_series.atr_14[-1]
        m15_atr = m15_series.atr_14[-1]
        if h1_atr <= 0 or m15_atr <= 0:
            return None

        h1_last = h1_window[-1]
        m15_last = m15_window[-1]
        m15_prev = m15_window[-2]

        # 3. H1 Macro Bias using EMA 21 & EMA 144 (falls back to 21 EMA slope if < 144 H1 bars)
        h1_ema21 = h1_series.ema_21[-1]
        h1_ema21_prev = h1_series.ema_21[-4]
        h1_ema144 = h1_series.ema_144[-1]
        h1_close_f = float(h1_last.close)

        if h1_ema144 is not None:
            ema_sep = abs(h1_ema21 - h1_ema144)
            if ema_sep < h1_atr * self.h1_min_ema_sep_atr:
                return None
            h1_bullish = h1_close_f > h1_ema144 and h1_ema21 > h1_ema144
            h1_bearish = h1_close_f < h1_ema144 and h1_ema21 < h1_ema144
        else:
            h1_bullish = h1_close_f > h1_ema21 and h1_ema21 > h1_ema21_prev
            h1_bearish = h1_close_f < h1_ema21 and h1_ema21 < h1_ema21_prev

        direction: Literal["BUY", "SELL"] | None = None
        if h1_bullish:
            direction = "BUY"
        elif h1_bearish:
            direction = "SELL"
        else:
            return None

        # 4. Closed M15 candle + EMA 21 reclaim + RSI momentum confirmation
        br = body_ratio(m15_last)
        if br < self.min_body_ratio:
            return None

        m15_ema21 = Decimal(str(m15_series.ema_21[-1]))
        m15_rsi = m15_series.rsi_14[-1]
        if direction == "BUY":
            candle_ok = (
                m15_last.close > m15_last.open
                and m15_last.close > m15_ema21
                and m15_last.close > m15_prev.close
                and 42.0 <= m15_rsi <= 72.0
            )
        else:
            candle_ok = (
                m15_last.close < m15_last.open
                and m15_last.close < m15_ema21
                and m15_last.close < m15_prev.close
                and 28.0 <= m15_rsi <= 58.0
            )
        if not candle_ok:
            return None

        # 5. Detect valid causal Elliott Wave pattern with all MACD + 81.2% + Cardinal rules
        atr_dec = Decimal(str(m15_atr))
        pattern = self._detect_best_pattern(
            m15_window=m15_window,
            m15_series=m15_series,
            h1_window=h1_window,
            h1_series=h1_series,
            direction=direction,
            atr_dec=atr_dec,
        )
        if pattern is None:
            return None

        # 6. Multi-Factor Independent Confluence Gate (NEVER buy on Fib touch alone!)
        smc = evaluate_smc_confluence(
            m15_bars=m15_window,
            h1_bars=h1_window,
            direction=direction,
            atr=atr_dec,
            pullback_extreme=pattern.invalidation_level,
        )
        sig_swings = detect_significant_swings_idm(m15_window[-60:])
        has_recent_idm_bos = any(s.confirmed_bos for s in sig_swings[-4:])

        # Require MSS (Structure Shift) + minimum independent SMC/L-S-M-D-F confluence
        if not smc.mss_confirmed:
            return None
        if smc.score < self.min_smc_confluence:
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

        # 7. Hard Invalidation SL + Fibonacci Target (R:R >= 2.0)
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
            "h1": {
                "ema_21": round(h1_ema21, 3),
                "ema_144": None if h1_ema144 is None else round(h1_ema144, 3),
                "macd": round(h1_series.macd_line[-1], 4),
                "atr_14": round(h1_atr, 3),
            },
            "m15": {
                "ema_21": round(m15_series.ema_21[-1], 3),
                "ema_144": (
                    None
                    if m15_series.ema_144[-1] is None
                    else round(m15_series.ema_144[-1], 3)
                ),
                "macd": round(m15_series.macd_line[-1], 4),
                "signal": round(m15_series.signal_line[-1], 4),
                "rsi_14": round(m15_rsi, 2),
                "atr_14": round(m15_atr, 3),
            },
            "bodyRatio": round(br, 3),
            "elliottWave": {
                "setupType": pattern.setup_type,
                "retracementRatio": pattern.retracement_ratio,
                "extensionRatio": pattern.extension_ratio,
                "invalidationLevel": str(pattern.invalidation_level),
                "fibTargetPrice": str(
                    _round_price(pattern.fib_target_price, self.price_digits)
                ),
                "anchorDivergences": pattern.anchor_divergences,
                "macdChecks": pattern.macd_checks,
                "emaChecks": pattern.ema_checks,
            },
            "smcConfluence": {
                "score": smc.score,
                "liquiditySweep": smc.liquidity_sweep,
                "shakeout": smc.shakeout,
                "crt": smc.crt_confirmed,
                "htfPinBar": smc.htf_hammer_or_pinbar,
                "mss": smc.mss_confirmed,
                "displacement": smc.displacement,
                "fvgCreated": smc.fvg_created,
                "followThrough": smc.follow_through,
                "volumeExpansion": smc.volume_expansion_trigger,
                "tappedPois": smc.tapped_pois,
                "idmBosConfirmed": has_recent_idm_bos,
            },
        }

        reasons = [
            f"H1 EMA 21/144 trend {direction} aligned",
            pattern.summary,
            *pattern.macd_checks[:2],
            *smc.reasons[:3],
            f"Confluence score={smc.score}/8 (RSI={m15_rsi:.1f}, body={br:.2f})",
        ]

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
            deterministic_reasons=reasons,
            invalidation_rules=[
                (
                    f"Elliott Wave {pattern.setup_type} invalidated if price "
                    f"breaches {pattern.invalidation_level}"
                ),
                "Wave-2 close beyond 81.2% or MACD below Base Point invalidates impulse",
            ],
            indicators=indicators_payload,
        )
