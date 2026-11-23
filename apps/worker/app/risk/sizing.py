"""Deterministic Standard & Cent position sizing with live MT5 profit/margin math."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal
from typing import Literal

from pydantic import BaseModel

from app.mt5.client import MT5Client
from app.mt5.symbols import VerifiedSymbolSpecs

# Official MetaTrader5 order type constants for order_calc_profit / order_calc_margin
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1


class SizingResult(BaseModel):
    approved: bool
    volume: Decimal
    risk_budget: Decimal
    estimated_sl_loss: Decimal
    estimated_costs: Decimal
    estimated_total_loss: Decimal
    estimated_loss_percent: Decimal
    estimated_margin: Decimal | None = None
    rejection_code: str | None = None
    rejection_reason: str | None = None


def align_volume_down(
    raw_volume: Decimal, volume_min: Decimal, volume_max: Decimal, volume_step: Decimal
) -> Decimal:
    """Floor-align volume to broker volume_step without exceeding volume_max."""
    if raw_volume < volume_min or volume_step <= 0:
        return Decimal("0")
    capped = min(raw_volume, volume_max)
    steps = ((capped - volume_min) / volume_step).to_integral_value(rounding=ROUND_DOWN)
    return (volume_min + (steps * volume_step)).quantize(volume_step)


def _calc_loss_and_costs(
    *,
    client: MT5Client,
    action: int,
    symbol_specs: VerifiedSymbolSpecs,
    volume: Decimal,
    entry_price: Decimal,
    stop_loss: Decimal,
    spread_price: Decimal,
    commission_per_lot: Decimal,
    slippage_points: int,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Return (sl_loss, estimated_costs, total_loss) in native account currency."""
    raw_profit = client.order_calc_profit(
        action,
        symbol_specs.symbol,
        float(volume),
        float(entry_price),
        float(stop_loss),
    )
    if raw_profit is None:
        return None

    sl_loss = abs(Decimal(str(raw_profit)))

    # Value of spread + slippage buffer in native account currency via tick value ratio
    slippage_price = symbol_specs.point * Decimal(max(0, slippage_points))
    friction_price = max(Decimal("0"), spread_price) + slippage_price
    if friction_price > 0 and symbol_specs.trade_tick_size > 0:
        friction_ticks = friction_price / symbol_specs.trade_tick_size
        friction_cost = friction_ticks * symbol_specs.trade_tick_value * volume
    else:
        friction_cost = Decimal("0")

    commission_cost = max(Decimal("0"), commission_per_lot) * volume
    estimated_costs = friction_cost + commission_cost
    total_loss = sl_loss + estimated_costs
    return sl_loss, estimated_costs, total_loss


def calculate_position_size(
    *,
    client: MT5Client,
    direction: Literal["BUY", "SELL"],
    symbol_specs: VerifiedSymbolSpecs,
    account_equity: Decimal,
    free_margin: Decimal,
    risk_percent: Decimal,
    entry_price: Decimal,
    stop_loss: Decimal,
    spread_price: Decimal,
    commission_per_lot: Decimal = Decimal("0"),
    slippage_points: int = 0,
    margin_safety_multiplier: Decimal = Decimal("1.5"),
) -> SizingResult:
    """Compute largest broker-valid volume within risk_budget & margin buffer.

    Runs strictly in the broker account's native currency (USC for Standard Cent,
    USD for Standard). Rejects trade if volume_min exceeds risk_budget.
    """
    if account_equity <= 0 or risk_percent <= 0:
        return SizingResult(
            approved=False,
            volume=Decimal("0"),
            risk_budget=Decimal("0"),
            estimated_sl_loss=Decimal("0"),
            estimated_costs=Decimal("0"),
            estimated_total_loss=Decimal("0"),
            estimated_loss_percent=Decimal("0"),
            rejection_code="INVALID_EQUITY_OR_RISK",
            rejection_reason="Account equity and risk_percent must be positive",
        )

    risk_budget = (account_equity * risk_percent / Decimal("100")).quantize(
        Decimal("0.0001")
    )
    action = ORDER_TYPE_BUY if direction == "BUY" else ORDER_TYPE_SELL

    # Step 1: Evaluate at broker minimum volume first (fail fast on small accounts)
    min_eval = _calc_loss_and_costs(
        client=client,
        action=action,
        symbol_specs=symbol_specs,
        volume=symbol_specs.volume_min,
        entry_price=entry_price,
        stop_loss=stop_loss,
        spread_price=spread_price,
        commission_per_lot=commission_per_lot,
        slippage_points=slippage_points,
    )
    if min_eval is None:
        return SizingResult(
            approved=False,
            volume=Decimal("0"),
            risk_budget=risk_budget,
            estimated_sl_loss=Decimal("0"),
            estimated_costs=Decimal("0"),
            estimated_total_loss=Decimal("0"),
            estimated_loss_percent=Decimal("0"),
            rejection_code="MT5_PROFIT_CALC_FAILED",
            rejection_reason="MT5 order_calc_profit returned None",
        )

    min_sl_loss, min_costs, min_total_loss = min_eval
    if min_total_loss <= 0:
        return SizingResult(
            approved=False,
            volume=Decimal("0"),
            risk_budget=risk_budget,
            estimated_sl_loss=min_sl_loss,
            estimated_costs=min_costs,
            estimated_total_loss=min_total_loss,
            estimated_loss_percent=Decimal("0"),
            rejection_code="INVALID_SL_LOSS",
            rejection_reason="Calculated loss at minimum lot must be positive",
        )

    if min_total_loss > risk_budget:
        loss_pct = ((min_total_loss / account_equity) * Decimal("100")).quantize(
            Decimal("0.0001")
        )
        return SizingResult(
            approved=False,
            volume=Decimal("0"),
            risk_budget=risk_budget,
            estimated_sl_loss=min_sl_loss.quantize(Decimal("0.0001")),
            estimated_costs=min_costs.quantize(Decimal("0.0001")),
            estimated_total_loss=min_total_loss.quantize(Decimal("0.0001")),
            estimated_loss_percent=loss_pct,
            rejection_code="MINIMUM_LOT_EXCEEDS_RISK_BUDGET",
            rejection_reason=(
                f"Minimum lot {symbol_specs.volume_min} estimated loss "
                f"({min_total_loss:.4f}) exceeds risk budget ({risk_budget:.4f})"
            ),
        )

    # Step 2: Scale linearly from min lot and floor-align to volume_step
    scale = risk_budget / min_total_loss
    candidate_vol = align_volume_down(
        symbol_specs.volume_min * scale,
        symbol_specs.volume_min,
        symbol_specs.volume_max,
        symbol_specs.volume_step,
    )

    # Step 3: Verify exact MT5 profit & margin at candidate_vol; step down if needed
    while candidate_vol >= symbol_specs.volume_min:
        ev = _calc_loss_and_costs(
            client=client,
            action=action,
            symbol_specs=symbol_specs,
            volume=candidate_vol,
            entry_price=entry_price,
            stop_loss=stop_loss,
            spread_price=spread_price,
            commission_per_lot=commission_per_lot,
            slippage_points=slippage_points,
        )
        if ev is None:
            return SizingResult(
                approved=False,
                volume=Decimal("0"),
                risk_budget=risk_budget,
                estimated_sl_loss=Decimal("0"),
                estimated_costs=Decimal("0"),
                estimated_total_loss=Decimal("0"),
                estimated_loss_percent=Decimal("0"),
                rejection_code="MT5_PROFIT_CALC_FAILED",
                rejection_reason="MT5 order_calc_profit failed during volume verification",
            )

        sl_loss, costs, total_loss = ev
        raw_margin = client.order_calc_margin(
            action, symbol_specs.symbol, float(candidate_vol), float(entry_price)
        )
        if raw_margin is None:
            return SizingResult(
                approved=False,
                volume=Decimal("0"),
                risk_budget=risk_budget,
                estimated_sl_loss=sl_loss.quantize(Decimal("0.0001")),
                estimated_costs=costs.quantize(Decimal("0.0001")),
                estimated_total_loss=total_loss.quantize(Decimal("0.0001")),
                estimated_loss_percent=Decimal("0"),
                rejection_code="MT5_MARGIN_CALC_FAILED",
                rejection_reason="MT5 order_calc_margin returned None",
            )

        req_margin = Decimal(str(raw_margin))
        margin_ok = free_margin >= (req_margin * margin_safety_multiplier)
        if total_loss <= risk_budget and margin_ok:
            loss_pct = ((total_loss / account_equity) * Decimal("100")).quantize(
                Decimal("0.0001")
            )
            return SizingResult(
                approved=True,
                volume=candidate_vol,
                risk_budget=risk_budget,
                estimated_sl_loss=sl_loss.quantize(Decimal("0.0001")),
                estimated_costs=costs.quantize(Decimal("0.0001")),
                estimated_total_loss=total_loss.quantize(Decimal("0.0001")),
                estimated_loss_percent=loss_pct,
                estimated_margin=req_margin.quantize(Decimal("0.0001")),
            )

        next_vol = candidate_vol - symbol_specs.volume_step
        if next_vol < symbol_specs.volume_min:
            return SizingResult(
                approved=False,
                volume=Decimal("0"),
                risk_budget=risk_budget,
                estimated_sl_loss=sl_loss.quantize(Decimal("0.0001")),
                estimated_costs=costs.quantize(Decimal("0.0001")),
                estimated_total_loss=total_loss.quantize(Decimal("0.0001")),
                estimated_loss_percent=(
                    (total_loss / account_equity) * Decimal("100")
                ).quantize(Decimal("0.0001")),
                estimated_margin=req_margin.quantize(Decimal("0.0001")),
                rejection_code="INSUFFICIENT_FREE_MARGIN",
                rejection_reason=(
                    f"Free margin ({free_margin:.4f}) below required safety buffer "
                    f"({req_margin * margin_safety_multiplier:.4f})"
                ),
            )
        candidate_vol = next_vol.quantize(symbol_specs.volume_step)

    return SizingResult(
        approved=False,
        volume=Decimal("0"),
        risk_budget=risk_budget,
        estimated_sl_loss=Decimal("0"),
        estimated_costs=Decimal("0"),
        estimated_total_loss=Decimal("0"),
        estimated_loss_percent=Decimal("0"),
        rejection_code="NO_VALID_VOLUME",
        rejection_reason="Unable to compute a valid volume",
    )
