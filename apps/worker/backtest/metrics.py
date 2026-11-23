"""Backtest performance metrics and walk-forward summary calculations."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class SimulatedTrade(BaseModel):
    direction: Literal["BUY", "SELL"]
    opened_at: datetime
    closed_at: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    volume: Decimal
    gross_pnl: Decimal
    commission: Decimal
    swap: Decimal
    net_pnl: Decimal
    r_multiple: Decimal
    exit_reason: Literal["SL", "TP", "END_OF_DATA"]


class BacktestMetrics(BaseModel):
    initial_equity: Decimal
    final_equity: Decimal
    total_trades: int
    wins: int
    losses: int
    win_rate_percent: float
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: float | None
    expectancy: Decimal
    max_drawdown_percent: float
    average_r: float
    max_consecutive_losses: int
    total_commission: Decimal
    total_swap: Decimal


def compute_metrics(
    trades: list[SimulatedTrade], initial_equity: Decimal
) -> BacktestMetrics:
    equity = initial_equity
    peak = initial_equity
    max_dd = 0.0

    wins = 0
    losses = 0
    gross_profit = Decimal("0")
    gross_loss = Decimal("0")
    total_commission = Decimal("0")
    total_swap = Decimal("0")
    r_sum = Decimal("0")
    streak = 0
    max_streak = 0

    for t in trades:
        equity += t.net_pnl
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = float(((peak - equity) / peak) * Decimal("100"))
            if dd > max_dd:
                max_dd = dd

        total_commission += t.commission
        total_swap += t.swap
        r_sum += t.r_multiple

        if t.net_pnl > 0:
            wins += 1
            gross_profit += t.net_pnl
            streak = 0
        else:
            losses += 1
            gross_loss += abs(t.net_pnl)
            streak += 1
            if streak > max_streak:
                max_streak = streak

    n = len(trades)
    net_pnl = equity - initial_equity
    win_rate = round((wins / n) * 100.0, 2) if n > 0 else 0.0
    profit_factor = (
        round(float(gross_profit / gross_loss), 3)
        if gross_loss > 0
        else (None if gross_profit == 0 else 999.0)
    )
    expectancy = (net_pnl / Decimal(n)).quantize(Decimal("0.0001")) if n > 0 else Decimal("0")
    avg_r = round(float(r_sum / Decimal(n)), 3) if n > 0 else 0.0

    return BacktestMetrics(
        initial_equity=initial_equity,
        final_equity=equity.quantize(Decimal("0.0001")),
        total_trades=n,
        wins=wins,
        losses=losses,
        win_rate_percent=win_rate,
        net_pnl=net_pnl.quantize(Decimal("0.0001")),
        gross_profit=gross_profit.quantize(Decimal("0.0001")),
        gross_loss=gross_loss.quantize(Decimal("0.0001")),
        profit_factor=profit_factor,
        expectancy=expectancy,
        max_drawdown_percent=round(max_dd, 3),
        average_r=avg_r,
        max_consecutive_losses=max_streak,
        total_commission=total_commission.quantize(Decimal("0.0001")),
        total_swap=total_swap.quantize(Decimal("0.0001")),
    )
