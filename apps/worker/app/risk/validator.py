"""Unified deterministic pre-trade risk validation pipeline (Architecture Section 7.4)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.market.features import (
    BlackoutWindowSpec,
    active_blackout_at,
    is_in_rollover_break,
    is_weekend_market_closed,
)
from app.mt5.account import VerifiedAccount
from app.mt5.client import MT5Client
from app.mt5.market_data import LiveQuote
from app.mt5.symbols import VerifiedSymbolSpecs
from app.risk.limits import (
    RiskSnapshotState,
    evaluate_loss_limits,
    validate_stop_and_freeze_levels,
)
from app.risk.sizing import SizingResult, calculate_position_size


class PreTradeRiskContext(BaseModel):
    account: VerifiedAccount
    symbol_specs: VerifiedSymbolSpecs
    quote: LiveQuote
    direction: Literal["BUY", "SELL"]
    entry_reference: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    now_utc: datetime

    # Runtime settings & limits
    kill_switch_active: bool = False
    is_paused: bool = False
    risk_percent: Decimal = Decimal("0.25")
    min_reward_risk: Decimal = Decimal("2.00")
    max_spread: Decimal = Decimal("0.50")
    max_deviation_points: int = 30
    max_quote_age_seconds: float = 15.0
    commission_per_lot: Decimal = Decimal("0.00")
    slippage_points: int = 5
    margin_safety_multiplier: Decimal = Decimal("1.5")

    # Exposure & time filters
    open_positions_count: int = 0
    max_open_positions: int = 1
    daily_trades_count: int = 0
    max_daily_trades: int = 5
    pending_proposals_count: int = 0
    max_pending_proposals: int = 3
    check_pending_limit: bool = True
    rollover_start_utc: str = "21:00"
    rollover_end_utc: str = "22:00"
    blackouts: list[BlackoutWindowSpec] = Field(default_factory=list)
    daily_risk: RiskSnapshotState | None = None
    weekly_risk: RiskSnapshotState | None = None

    # Execution-specific revalidation
    check_price_drift: bool = False
    require_trade_allowed: bool = True


class RiskValidationResult(BaseModel):
    approved: bool
    volume: Decimal = Decimal("0")
    risk_budget: Decimal = Decimal("0")
    estimated_total_loss: Decimal = Decimal("0")
    estimated_loss_percent: Decimal = Decimal("0")
    estimated_margin: Decimal | None = None
    reward_risk: Decimal = Decimal("0")
    sizing: SizingResult | None = None
    rejection_codes: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)


def validate_pre_trade(
    client: MT5Client, ctx: PreTradeRiskContext
) -> RiskValidationResult:
    """Run all deterministic pre-trade checks; fail closed on any violation."""
    codes: list[str] = []
    reasons: list[str] = []

    if ctx.kill_switch_active:
        codes.append("KILL_SWITCH_ACTIVE")
        reasons.append("Kill switch is active — all new orders are blocked")

    if ctx.is_paused:
        codes.append("SYSTEM_PAUSED")
        reasons.append("Trading system is currently paused")

    if not ctx.account.terminal_connected:
        codes.append("MT5_DISCONNECTED")
        reasons.append("MT5 terminal is disconnected")

    if ctx.require_trade_allowed:
        if not ctx.account.terminal_trade_allowed:
            codes.append("ALGO_TRADING_DISABLED")
            reasons.append("MT5 terminal Algo Trading is disabled")
        if not ctx.account.account_trade_allowed:
            codes.append("ACCOUNT_TRADING_DISABLED")
            reasons.append("MT5 account trading permission is disabled")

    if ctx.quote.is_stale(ctx.max_quote_age_seconds, now_utc=ctx.now_utc):
        codes.append("STALE_QUOTE")
        reasons.append("Market quote timestamp is stale")

    if ctx.quote.spread > ctx.max_spread:
        codes.append("SPREAD_EXCEEDED")
        reasons.append(
            f"Spread ({ctx.quote.spread}) exceeds max_spread ({ctx.max_spread})"
        )

    if is_weekend_market_closed(ctx.now_utc):
        codes.append("MARKET_CLOSED")
        reasons.append("Market is closed for the weekend")

    if is_in_rollover_break(
        ctx.now_utc,
        rollover_start_utc=ctx.rollover_start_utc,
        rollover_end_utc=ctx.rollover_end_utc,
    ):
        codes.append("ROLLOVER_BLACKOUT")
        reasons.append("Daily XAUUSD rollover break active")

    blackout = active_blackout_at(ctx.now_utc, ctx.blackouts)
    if blackout is not None:
        codes.append("NEWS_BLACKOUT")
        reasons.append(f"Active news blackout window: {blackout.title}")

    if ctx.open_positions_count >= ctx.max_open_positions:
        codes.append("MAX_POSITIONS_REACHED")
        reasons.append(
            f"Open positions ({ctx.open_positions_count}) reached max ({ctx.max_open_positions})"
        )

    if ctx.daily_trades_count >= ctx.max_daily_trades:
        codes.append("DAILY_TRADE_LIMIT_REACHED")
        reasons.append(
            f"Daily trade limit reached ({ctx.daily_trades_count}/{ctx.max_daily_trades})"
        )

    if ctx.check_pending_limit and ctx.pending_proposals_count >= ctx.max_pending_proposals:
        codes.append("MAX_PENDING_PROPOSALS")
        reasons.append(
            f"Pending proposals ({ctx.pending_proposals_count}) reached max "
            f"({ctx.max_pending_proposals})"
        )

    dd = evaluate_loss_limits(
        current_equity=ctx.account.equity,
        daily=ctx.daily_risk,
        weekly=ctx.weekly_risk,
    )
    if dd.daily_tripped:
        codes.append("DAILY_LOSS_LIMIT")
    if dd.weekly_tripped:
        codes.append("WEEKLY_LOSS_LIMIT")
    reasons.extend(dd.reasons)

    live_entry = ctx.quote.ask if ctx.direction == "BUY" else ctx.quote.bid
    if ctx.check_price_drift:
        max_drift = ctx.symbol_specs.point * Decimal(ctx.max_deviation_points)
        drift = abs(live_entry - ctx.entry_reference)
        if drift > max_drift:
            codes.append("PRICE_DEVIATION_EXCEEDED")
            reasons.append(
                f"Live price drifted ({drift}) beyond max allowed deviation ({max_drift})"
            )

    stops_ok, rr, stop_errors = validate_stop_and_freeze_levels(
        direction=ctx.direction,
        entry_price=live_entry,
        stop_loss=ctx.stop_loss,
        take_profit=ctx.take_profit,
        bid=ctx.quote.bid,
        ask=ctx.quote.ask,
        symbol_specs=ctx.symbol_specs,
        min_reward_risk=ctx.min_reward_risk,
    )
    if not stops_ok:
        codes.append("INVALID_STOPS_OR_RR")
        reasons.extend(stop_errors)

    if codes or ctx.stop_loss is None:
        return RiskValidationResult(
            approved=False,
            reward_risk=rr,
            rejection_codes=codes or ["MISSING_STOP_LOSS"],
            rejection_reasons=reasons or ["Mandatory Stop Loss is missing"],
        )

    sizing = calculate_position_size(
        client=client,
        direction=ctx.direction,
        symbol_specs=ctx.symbol_specs,
        account_equity=ctx.account.equity,
        free_margin=ctx.account.free_margin,
        risk_percent=ctx.risk_percent,
        entry_price=live_entry,
        stop_loss=ctx.stop_loss,
        spread_price=ctx.quote.spread,
        commission_per_lot=ctx.commission_per_lot,
        slippage_points=ctx.slippage_points,
        margin_safety_multiplier=ctx.margin_safety_multiplier,
    )
    if not sizing.approved:
        return RiskValidationResult(
            approved=False,
            risk_budget=sizing.risk_budget,
            estimated_total_loss=sizing.estimated_total_loss,
            estimated_loss_percent=sizing.estimated_loss_percent,
            estimated_margin=sizing.estimated_margin,
            reward_risk=rr,
            sizing=sizing,
            rejection_codes=[sizing.rejection_code or "SIZING_REJECTED"],
            rejection_reasons=[sizing.rejection_reason or "Position sizing rejected"],
        )

    return RiskValidationResult(
        approved=True,
        volume=sizing.volume,
        risk_budget=sizing.risk_budget,
        estimated_total_loss=sizing.estimated_total_loss,
        estimated_loss_percent=sizing.estimated_loss_percent,
        estimated_margin=sizing.estimated_margin,
        reward_risk=rr,
        sizing=sizing,
    )
