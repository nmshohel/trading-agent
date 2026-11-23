"""Phase 4 risk engine tests: Standard vs Cent sizing, costs, drawdown & pre-trade gates."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.market.features import BlackoutWindowSpec
from app.mt5.account import VerifiedAccount
from app.mt5.client import MT5Client
from app.mt5.market_data import LiveQuote
from app.mt5.symbols import VerifiedSymbolSpecs
from app.risk.limits import (
    DEAL_TYPE_BALANCE,
    RiskSnapshotState,
    adjust_baseline_for_balance_deals,
    compute_drawdown_percent,
    evaluate_loss_limits,
)
from app.risk.sizing import ORDER_TYPE_BUY, calculate_position_size
from app.risk.validator import PreTradeRiskContext, validate_pre_trade


class FakeMT5Risk:
    """Simulates MT5 order_calc_profit and order_calc_margin for Standard & Cent."""

    def __init__(
        self,
        *,
        contract_size: float = 100.0,
        margin_per_lot: float = 150.0,
    ) -> None:
        self.contract_size = contract_size
        self.margin_per_lot = margin_per_lot

    def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float:
        delta = (
            (price_close - price_open)
            if action == ORDER_TYPE_BUY
            else (price_open - price_close)
        )
        # On Standard (100 oz, USD): 0.01 lot * 100 * $1 = $1.00
        # On Standard Cent (100 cent-oz, USC): 0.01 lot * 100 * $1 = 1.00 USC ($0.01)
        return round(delta * self.contract_size * volume, 4)

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float:
        return round(self.margin_per_lot * volume, 4)


def _specs(symbol: str = "XAUUSDc", **overrides: Any) -> VerifiedSymbolSpecs:
    base: dict[str, Any] = {
        "symbol": symbol,
        "volume_min": Decimal("0.01"),
        "volume_max": Decimal("200.00"),
        "volume_step": Decimal("0.01"),
        "trade_contract_size": Decimal("100.0"),
        "trade_tick_size": Decimal("0.01"),
        "trade_tick_value": Decimal("1.0"),
        "point": Decimal("0.01"),
        "digits": 2,
        "trade_stops_level": 10,
        "trade_freeze_level": 0,
        "filling_mode": "IOC",
        "filling_mode_flags": 2,
        "expiration_mode_flags": 15,
        "trade_mode": 4,
    }
    base.update(overrides)
    return VerifiedSymbolSpecs(**base)


def _account(
    *,
    account_type: str = "CENT",
    currency: str = "USC",
    scale: int = 100,
    equity: str = "10000.00",
    free_margin: str = "9800.00",
    terminal_trade_allowed: bool = True,
) -> VerifiedAccount:
    return VerifiedAccount(
        login="12345678",
        server="Exness-MT5Demo",
        account_currency=currency,
        currency_scale=scale,
        account_type=account_type,  # type: ignore[arg-type]
        margin_mode="HEDGING",
        mode="DEMO",
        balance=Decimal(equity),
        equity=Decimal(equity),
        free_margin=Decimal(free_margin),
        terminal_connected=True,
        terminal_trade_allowed=terminal_trade_allowed,
        account_trade_allowed=True,
    )


def test_standard_100_usd_account_rejects_normal_gold_sl() -> None:
    """$100 Standard account at 0.25% ($0.25 budget) MUST reject a $5 SL at 0.01 lot."""
    client = MT5Client(mt5_module=FakeMT5Risk(contract_size=100.0))
    res = calculate_position_size(
        client=client,
        direction="BUY",
        symbol_specs=_specs("XAUUSDm"),
        account_equity=Decimal("100.00"),
        free_margin=Decimal("100.00"),
        risk_percent=Decimal("0.25"),
        entry_price=Decimal("2650.20"),
        stop_loss=Decimal("2645.20"),  # $5 move -> $5.00 loss at 0.01 lot
        spread_price=Decimal("0.20"),
    )
    assert res.approved is False
    assert res.rejection_code == "MINIMUM_LOT_EXCEEDS_RISK_BUDGET"
    assert res.volume == Decimal("0")
    assert res.risk_budget == Decimal("0.2500")


def test_cent_10000_usc_account_sizes_xauusdc_accurately_with_costs() -> None:
    """10,000 USC ($100) Cent account at 0.50% (50 USC budget) fits $5 SL cleanly."""
    client = MT5Client(mt5_module=FakeMT5Risk(contract_size=100.0, margin_per_lot=150.0))
    res = calculate_position_size(
        client=client,
        direction="BUY",
        symbol_specs=_specs("XAUUSDc"),
        account_equity=Decimal("10000.00"),
        free_margin=Decimal("9800.00"),
        risk_percent=Decimal("0.50"),  # 50.00 USC risk budget
        entry_price=Decimal("2650.20"),
        stop_loss=Decimal("2645.20"),  # $5 move -> 5.00 USC per 0.01 lot
        spread_price=Decimal("0.20"),  # +0.20 USC per 0.01 lot
        commission_per_lot=Decimal("7.00"),  # +0.07 USC per 0.01 lot
        slippage_points=5,  # +0.05 USC per 0.01 lot
    )
    assert res.approved is True
    # Total per 0.01 lot = 5.00 + 0.20 + 0.07 + 0.05 = 5.32 USC -> 50 / 5.32 = 9 steps -> 0.09 lot
    assert res.volume == Decimal("0.09")
    assert res.estimated_total_loss <= Decimal("50.0000")
    assert res.estimated_costs > Decimal("0")


def test_margin_safety_buffer_rejects_when_free_margin_too_low() -> None:
    client = MT5Client(mt5_module=FakeMT5Risk(contract_size=100.0, margin_per_lot=50000.0))
    res = calculate_position_size(
        client=client,
        direction="BUY",
        symbol_specs=_specs("XAUUSDc"),
        account_equity=Decimal("10000.00"),
        free_margin=Decimal("400.00"),  # 0.01 lot needs 500 * 1.5 = 750 > 400
        risk_percent=Decimal("0.50"),
        entry_price=Decimal("2650.20"),
        stop_loss=Decimal("2645.20"),
        spread_price=Decimal("0.20"),
    )
    assert res.approved is False
    assert res.rejection_code == "INSUFFICIENT_FREE_MARGIN"


def test_equity_drawdown_weekly_lock_and_balance_deal_adjustment() -> None:
    assert compute_drawdown_percent(Decimal("10000"), Decimal("9850")) == Decimal("1.5000")

    # Manual deposit of +5000 USC should raise baseline from 10000 -> 15000
    adjusted, delta = adjust_baseline_for_balance_deals(
        Decimal("10000"),
        [{"type": DEAL_TYPE_BALANCE, "profit": 5000.0}, {"type": 0, "profit": -20.0}],
    )
    assert adjusted == Decimal("15000.0")
    assert delta == Decimal("5000.0")

    # Tripped weekly limit stays blocked even if equity recovered, until reset_at is set
    dd_locked = evaluate_loss_limits(
        current_equity=Decimal("9950"),
        daily=RiskSnapshotState(
            starting_equity=Decimal("10000"),
            loss_limit_percent=Decimal("1.00"),
        ),
        weekly=RiskSnapshotState(
            starting_equity=Decimal("10000"),
            loss_limit_percent=Decimal("2.00"),
            is_tripped=True,
            reset_at=None,
        ),
    )
    assert dd_locked.daily_tripped is False
    assert dd_locked.weekly_tripped is True

    # Once manually reset, weekly lock clears if current drawdown < limit
    dd_reset = evaluate_loss_limits(
        current_equity=Decimal("9950"),
        daily=None,
        weekly=RiskSnapshotState(
            starting_equity=Decimal("10000"),
            loss_limit_percent=Decimal("2.00"),
            is_tripped=True,
            reset_at=datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
        ),
    )
    assert dd_reset.weekly_tripped is False


def test_validate_pre_trade_passes_valid_cent_setup_and_blocks_all_hazards() -> None:
    client = MT5Client(mt5_module=FakeMT5Risk(contract_size=100.0, margin_per_lot=150.0))
    now = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)
    quote = LiveQuote(
        symbol="XAUUSDc",
        bid=Decimal("2650.00"),
        ask=Decimal("2650.20"),
        spread=Decimal("0.20"),
        quote_time=now - timedelta(seconds=2),
    )
    base = PreTradeRiskContext(
        account=_account(),
        symbol_specs=_specs("XAUUSDc"),
        quote=quote,
        direction="BUY",
        entry_reference=Decimal("2650.20"),
        stop_loss=Decimal("2645.20"),
        take_profit=Decimal("2660.20"),
        now_utc=now,
        risk_percent=Decimal("0.50"),
    )

    ok = validate_pre_trade(client, base)
    assert ok.approved is True
    assert ok.volume >= Decimal("0.01")
    assert ok.reward_risk == Decimal("2.00")

    # Kill switch blocks
    ks = validate_pre_trade(client, base.model_copy(update={"kill_switch_active": True}))
    assert ks.approved is False
    assert "KILL_SWITCH_ACTIVE" in ks.rejection_codes

    # Price drift beyond max_deviation_points (30 * 0.01 = 0.30) blocks during execution
    drift = validate_pre_trade(
        client,
        base.model_copy(
            update={"check_price_drift": True, "entry_reference": Decimal("2649.50")}
        ),
    )
    assert drift.approved is False
    assert "PRICE_DEVIATION_EXCEEDED" in drift.rejection_codes

    # Missing Stop Loss blocks
    no_sl = validate_pre_trade(client, base.model_copy(update={"stop_loss": None}))
    assert no_sl.approved is False
    assert "INVALID_STOPS_OR_RR" in no_sl.rejection_codes

    # Active blackout blocks
    bo = BlackoutWindowSpec(
        id="bo_1",
        title="FOMC",
        starts_at=now - timedelta(minutes=5),
        ends_at=now + timedelta(minutes=15),
    )
    bo_res = validate_pre_trade(client, base.model_copy(update={"blackouts": [bo]}))
    assert bo_res.approved is False
    assert "NEWS_BLACKOUT" in bo_res.rejection_codes

    # Max open positions blocks
    pos_res = validate_pre_trade(
        client, base.model_copy(update={"open_positions_count": 1})
    )
    assert pos_res.approved is False
    assert "MAX_POSITIONS_REACHED" in pos_res.rejection_codes
