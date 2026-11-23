"""Equity-based daily/weekly drawdown limits, balance adjustments, and stop checks."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel

from app.mt5.symbols import VerifiedSymbolSpecs

# Official MetaTrader5 deal type for manual deposits / withdrawals
DEAL_TYPE_BALANCE = 2


class RiskSnapshotState(BaseModel):
    starting_equity: Decimal
    loss_limit_percent: Decimal
    is_tripped: bool = False
    reset_at: datetime | None = None


class DrawdownCheckResult(BaseModel):
    daily_drawdown_percent: Decimal
    weekly_drawdown_percent: Decimal
    daily_tripped: bool
    weekly_tripped: bool
    reasons: list[str]


def compute_drawdown_percent(
    starting_equity: Decimal, current_equity: Decimal
) -> Decimal:
    """Equity-based drawdown % capturing both realized P/L and floating drawdown."""
    if starting_equity <= 0:
        return Decimal("100.0000")
    dd = ((starting_equity - current_equity) / starting_equity) * Decimal("100")
    return max(Decimal("0"), dd).quantize(Decimal("0.0001"))


def adjust_baseline_for_balance_deals(
    starting_equity: Decimal, deals: list[Any]
) -> tuple[Decimal, Decimal]:
    """Adjust starting_equity by net DEPOSIT/WITHDRAWAL deals (DEAL_TYPE_BALANCE).

    Returns (adjusted_starting_equity, net_balance_change).
    """
    net_change = Decimal("0")
    for deal in deals:
        deal_type = (
            deal.get("type") if isinstance(deal, dict) else getattr(deal, "type", -1)
        )
        if int(deal_type) == DEAL_TYPE_BALANCE:
            profit = (
                deal.get("profit", 0)
                if isinstance(deal, dict)
                else getattr(deal, "profit", 0)
            )
            net_change += Decimal(str(profit))
    return starting_equity + net_change, net_change


def evaluate_loss_limits(
    *,
    current_equity: Decimal,
    daily: RiskSnapshotState | None,
    weekly: RiskSnapshotState | None,
) -> DrawdownCheckResult:
    reasons: list[str] = []
    daily_dd = Decimal("0")
    weekly_dd = Decimal("0")
    daily_tripped = False
    weekly_tripped = False

    if daily is not None:
        daily_dd = compute_drawdown_percent(daily.starting_equity, current_equity)
        if daily.is_tripped or daily_dd >= daily.loss_limit_percent:
            daily_tripped = True
            reasons.append(
                f"Daily loss limit tripped ({daily_dd:.2f}% >= {daily.loss_limit_percent:.2f}%)"
            )

    if weekly is not None:
        weekly_dd = compute_drawdown_percent(weekly.starting_equity, current_equity)
        # Weekly limit requires explicit manual reset (reset_at) once tripped
        if weekly.is_tripped and weekly.reset_at is None:
            weekly_tripped = True
            reasons.append("Weekly loss limit tripped — manual review & reset required")
        elif weekly_dd >= weekly.loss_limit_percent:
            weekly_tripped = True
            reasons.append(
                f"Weekly loss limit exceeded ({weekly_dd:.2f}% >= {weekly.loss_limit_percent:.2f}%)"
            )

    return DrawdownCheckResult(
        daily_drawdown_percent=daily_dd,
        weekly_drawdown_percent=weekly_dd,
        daily_tripped=daily_tripped,
        weekly_tripped=weekly_tripped,
        reasons=reasons,
    )


def validate_stop_and_freeze_levels(
    *,
    direction: Literal["BUY", "SELL"],
    entry_price: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    bid: Decimal,
    ask: Decimal,
    symbol_specs: VerifiedSymbolSpecs,
    min_reward_risk: Decimal,
) -> tuple[bool, Decimal, list[str]]:
    """Validate mandatory SL, TP side, minimum R:R, and broker stop/freeze distances."""
    errors: list[str] = []

    if stop_loss is None or stop_loss <= 0:
        return False, Decimal("0"), ["Mandatory Stop Loss is missing or non-positive"]

    min_stop_dist = symbol_specs.point * Decimal(
        max(symbol_specs.trade_stops_level, symbol_specs.trade_freeze_level)
    )

    if direction == "BUY":
        if stop_loss >= bid:
            errors.append("BUY Stop Loss must be strictly below current bid")
        if (bid - stop_loss) < min_stop_dist:
            errors.append(
                f"BUY Stop Loss distance ({bid - stop_loss}) "
                f"below broker stops_level ({min_stop_dist})"
            )
        if take_profit is not None:
            if take_profit <= ask:
                errors.append("BUY Take Profit must be strictly above current ask")
            if (take_profit - ask) < min_stop_dist:
                errors.append(
                    f"BUY Take Profit distance ({take_profit - ask}) "
                    f"below broker stops_level ({min_stop_dist})"
                )
        risk_dist = entry_price - stop_loss
        reward_dist = (take_profit - entry_price) if take_profit is not None else Decimal("0")
    else:
        if stop_loss <= ask:
            errors.append("SELL Stop Loss must be strictly above current ask")
        if (stop_loss - ask) < min_stop_dist:
            errors.append(
                f"SELL Stop Loss distance ({stop_loss - ask}) "
                f"below broker stops_level ({min_stop_dist})"
            )
        if take_profit is not None:
            if take_profit >= bid:
                errors.append("SELL Take Profit must be strictly below current bid")
            if (bid - take_profit) < min_stop_dist:
                errors.append(
                    f"SELL Take Profit distance ({bid - take_profit}) "
                    f"below broker stops_level ({min_stop_dist})"
                )
        risk_dist = stop_loss - entry_price
        reward_dist = (entry_price - take_profit) if take_profit is not None else Decimal("0")

    if risk_dist <= 0:
        errors.append("Invalid risk distance between entry and Stop Loss")
        return False, Decimal("0"), errors

    rr = (reward_dist / risk_dist).quantize(Decimal("0.01")) if take_profit else Decimal("0")
    if take_profit is not None and rr < min_reward_risk:
        errors.append(
            f"Reward/Risk ({rr}) is below configured minimum ({min_reward_risk})"
        )

    return len(errors) == 0, rr, errors
