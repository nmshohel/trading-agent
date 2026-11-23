"""Event-driven bar-by-bar backtesting engine with spread, commission, swap & slippage."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from app.mt5.market_data import CandleBar
from app.strategy.base import BaseStrategy
from app.strategy.signal import StrategyContext
from app.strategy.xauusd_v1 import XauusdV1Strategy
from backtest.data_loader import (
    generate_synthetic_trend_m15,
    load_candles_csv,
    resample_to_h1,
)
from backtest.metrics import BacktestMetrics, SimulatedTrade, compute_metrics


@dataclass
class BacktestConfig:
    initial_equity: Decimal = Decimal("10000.00")  # e.g., 10,000 USC ($100 Cent)
    risk_percent: Decimal = Decimal("0.0025")  # 0.25%
    spread: Decimal = Decimal("0.20")
    slippage: Decimal = Decimal("0.05")
    commission_per_lot: Decimal = Decimal("0.00")
    daily_swap_per_lot: Decimal = Decimal("-0.50")  # Wednesday applies 3x swap
    value_per_point_per_lot: Decimal = Decimal("1.0")  # Cent: 1 USC per $1 move per 1.00 lot
    volume_min: Decimal = Decimal("0.01")
    volume_step: Decimal = Decimal("0.01")
    max_spread: Decimal = Decimal("0.50")


@dataclass
class _OpenPosition:
    direction: Literal["BUY", "SELL"]
    opened_at: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    volume: Decimal
    initial_risk_distance: Decimal
    swap_accumulated: Decimal = Decimal("0")
    last_rollover_date: datetime | None = None


def _compute_volume(
    equity: Decimal, risk_distance: Decimal, cfg: BacktestConfig
) -> Decimal | None:
    if risk_distance <= 0:
        return None
    risk_budget = equity * cfg.risk_percent
    loss_per_lot = (risk_distance * cfg.value_per_point_per_lot) + cfg.commission_per_lot
    if loss_per_lot <= 0:
        return None
    raw_lots = risk_budget / loss_per_lot
    steps = int(raw_lots / cfg.volume_step)
    vol = Decimal(steps) * cfg.volume_step
    if vol < cfg.volume_min:
        return None
    return vol


def _overnight_swap_multiplier(dt_utc: datetime) -> int:
    # XAUUSD triple swap occurs on Wednesday night rollover (weekday == 2)
    return 3 if dt_utc.weekday() == 2 else 1


def run_backtest(
    m15_bars: list[CandleBar],
    strategy: BaseStrategy | None = None,
    config: BacktestConfig | None = None,
) -> tuple[BacktestMetrics, list[SimulatedTrade]]:
    """Run conservative bar-by-bar replay using closed M15 and derived H1 candles."""
    cfg = config or BacktestConfig()
    strat = strategy or XauusdV1Strategy()
    ordered = sorted(m15_bars, key=lambda b: b.candle_time)
    if len(ordered) < 80:
        return compute_metrics([], cfg.initial_equity), []

    equity = cfg.initial_equity
    open_pos: _OpenPosition | None = None
    trades: list[SimulatedTrade] = []
    dedup_keys: set[str] = set()

    for idx in range(60, len(ordered)):
        bar = ordered[idx]
        bar_utc = bar.candle_time.astimezone(UTC)

        # 1. Manage open position on current bar (conservative: check SL before TP)
        if open_pos is not None:
            # Apply overnight swap once per calendar rollover crossing (21:00 UTC)
            if (
                bar_utc.hour >= 21
                and (
                    open_pos.last_rollover_date is None
                    or open_pos.last_rollover_date.date() < bar_utc.date()
                )
            ):
                mult = _overnight_swap_multiplier(bar_utc)
                open_pos.swap_accumulated += (
                    cfg.daily_swap_per_lot * open_pos.volume * Decimal(mult)
                )
                open_pos.last_rollover_date = bar_utc

            exit_price: Decimal | None = None
            exit_reason: Literal["SL", "TP"] | None = None

            if open_pos.direction == "BUY":
                if bar.low <= open_pos.stop_loss:
                    exit_price = open_pos.stop_loss - cfg.slippage
                    exit_reason = "SL"
                elif bar.high >= open_pos.take_profit:
                    exit_price = open_pos.take_profit
                    exit_reason = "TP"
            else:
                if bar.high >= open_pos.stop_loss:
                    exit_price = open_pos.stop_loss + cfg.slippage
                    exit_reason = "SL"
                elif bar.low <= open_pos.take_profit:
                    exit_price = open_pos.take_profit
                    exit_reason = "TP"

            if exit_price is not None and exit_reason is not None:
                signed_move = (
                    (exit_price - open_pos.entry_price)
                    if open_pos.direction == "BUY"
                    else (open_pos.entry_price - exit_price)
                )
                gross = signed_move * cfg.value_per_point_per_lot * open_pos.volume
                comm = cfg.commission_per_lot * open_pos.volume
                net = gross - comm + open_pos.swap_accumulated
                r_mult = (
                    signed_move / open_pos.initial_risk_distance
                    if open_pos.initial_risk_distance > 0
                    else Decimal("0")
                )
                equity += net
                trades.append(
                    SimulatedTrade(
                        direction=open_pos.direction,
                        opened_at=open_pos.opened_at,
                        closed_at=bar_utc,
                        entry_price=open_pos.entry_price,
                        exit_price=exit_price,
                        stop_loss=open_pos.stop_loss,
                        take_profit=open_pos.take_profit,
                        volume=open_pos.volume,
                        gross_pnl=gross,
                        commission=comm,
                        swap=open_pos.swap_accumulated,
                        net_pnl=net,
                        r_multiple=r_mult.quantize(Decimal("0.001")),
                        exit_reason=exit_reason,
                    )
                )
                open_pos = None
            continue

        # 2. Evaluate strategy on closed history up to `idx`
        history_m15 = ordered[: idx + 1]
        history_h1 = resample_to_h1(history_m15)
        if len(history_h1) < 10:
            continue

        bid = bar.close
        ask = bar.close + cfg.spread
        ctx = StrategyContext(
            symbol=bar.symbol,
            h1_bars=history_h1,
            m15_bars=history_m15[-120:],
            current_bid=bid,
            current_ask=ask,
            max_spread=cfg.max_spread,
            now_utc=bar_utc,
            existing_dedup_keys=dedup_keys,
        )
        candidate = strat.evaluate(ctx)
        if candidate is None:
            continue

        dedup_keys.add(candidate.dedup_key)
        entry_fill = (
            candidate.entry_reference + cfg.slippage
            if candidate.direction == "BUY"
            else candidate.entry_reference - cfg.slippage
        )
        risk_dist = abs(entry_fill - candidate.stop_loss)
        vol = _compute_volume(equity, risk_dist, cfg)
        if vol is None:
            continue

        open_pos = _OpenPosition(
            direction=candidate.direction,
            opened_at=bar_utc,
            entry_price=entry_fill,
            stop_loss=candidate.stop_loss,
            take_profit=candidate.take_profit,
            volume=vol,
            initial_risk_distance=risk_dist,
        )

    # Close any remaining open position at final bar close
    if open_pos is not None:
        last_bar = ordered[-1]
        exit_price = last_bar.close
        signed_move = (
            (exit_price - open_pos.entry_price)
            if open_pos.direction == "BUY"
            else (open_pos.entry_price - exit_price)
        )
        gross = signed_move * cfg.value_per_point_per_lot * open_pos.volume
        comm = cfg.commission_per_lot * open_pos.volume
        net = gross - comm + open_pos.swap_accumulated
        r_mult = signed_move / open_pos.initial_risk_distance
        trades.append(
            SimulatedTrade(
                direction=open_pos.direction,
                opened_at=open_pos.opened_at,
                closed_at=last_bar.candle_time.astimezone(UTC),
                entry_price=open_pos.entry_price,
                exit_price=exit_price,
                stop_loss=open_pos.stop_loss,
                take_profit=open_pos.take_profit,
                volume=open_pos.volume,
                gross_pnl=gross,
                commission=comm,
                swap=open_pos.swap_accumulated,
                net_pnl=net,
                r_multiple=r_mult.quantize(Decimal("0.001")),
                exit_reason="END_OF_DATA",
            )
        )

    return compute_metrics(trades, cfg.initial_equity), trades


def run_walk_forward(
    m15_bars: list[CandleBar],
    *,
    train_ratio: float = 0.7,
    strategy: BaseStrategy | None = None,
    config: BacktestConfig | None = None,
) -> dict[str, BacktestMetrics]:
    """Split bars chronologically into in-sample and out-of-sample windows."""
    ordered = sorted(m15_bars, key=lambda b: b.candle_time)
    split = int(len(ordered) * train_ratio)
    in_metrics, _ = run_backtest(ordered[:split], strategy=strategy, config=config)
    out_metrics, _ = run_backtest(ordered[split:], strategy=strategy, config=config)
    return {"in_sample": in_metrics, "out_of_sample": out_metrics}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="XAUUSD v1 Backtest Runner")
    parser.add_argument("--csv", type=str, default=None, help="Path to M15 UTC CSV file")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Run smoke backtest on synthetic trending M15 candles",
    )
    args = parser.parse_args(argv)

    if args.csv:
        bars = load_candles_csv(args.csv)
    else:
        bars = generate_synthetic_trend_m15(num_bars=400, direction="UP")

    strat = XauusdV1Strategy(ema_fast=10, ema_mid=20, ema_slow=50)
    metrics, _ = run_backtest(bars, strategy=strat)
    print(json.dumps(metrics.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
