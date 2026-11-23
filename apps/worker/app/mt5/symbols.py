"""MT5 live symbol verification and dynamic filling_mode resolution."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.mt5.client import MT5Client

# Official MetaTrader5 symbol constants
SYMBOL_TRADE_MODE_FULL = 4
SYMBOL_FILLING_FOK = 1
SYMBOL_FILLING_IOC = 2


class SymbolVerificationError(RuntimeError):
    """Raised when the configured gold symbol is missing, untradeable, or invalid."""


class VerifiedSymbolSpecs(BaseModel):
    symbol: str
    volume_min: Decimal
    volume_max: Decimal
    volume_step: Decimal
    trade_contract_size: Decimal
    trade_tick_size: Decimal
    trade_tick_value: Decimal
    point: Decimal
    digits: int
    trade_stops_level: int
    trade_freeze_level: int
    filling_mode: Literal["FOK", "IOC", "RETURN"]
    filling_mode_flags: int
    expiration_mode_flags: int
    trade_mode: int

    def to_api_payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "volumeMin": str(self.volume_min),
            "volumeMax": str(self.volume_max),
            "volumeStep": str(self.volume_step),
            "tradeContractSize": str(self.trade_contract_size),
            "tradeTickSize": str(self.trade_tick_size),
            "tradeTickValue": str(self.trade_tick_value),
            "point": str(self.point),
            "digits": self.digits,
            "tradeStopsLevel": self.trade_stops_level,
            "tradeFreezeLevel": self.trade_freeze_level,
            "fillingMode": self.filling_mode,
            "fillingModeFlags": self.filling_mode_flags,
            "expirationModeFlags": self.expiration_mode_flags,
            "tradeMode": self.trade_mode,
        }


def resolve_filling_mode(filling_mode_flags: int) -> Literal["FOK", "IOC", "RETURN"]:
    """Resolve broker-supported MT5 order filling mode from symbol.filling_mode bitmask.

    Prefer IOC (Immediate or Cancel) when supported, then FOK (Fill or Kill),
    falling back to RETURN for exchange-style symbols with flags == 0.
    """
    if filling_mode_flags < 0:
        raise SymbolVerificationError(
            f"Invalid negative filling_mode bitmask: {filling_mode_flags}"
        )
    if filling_mode_flags & SYMBOL_FILLING_IOC:
        return "IOC"
    if filling_mode_flags & SYMBOL_FILLING_FOK:
        return "FOK"
    return "RETURN"


def verify_symbol(client: MT5Client, symbol: str) -> VerifiedSymbolSpecs:
    """Verify configured symbol exists, is visible, tradeable, and has valid live specs."""
    info = client.symbol_info(symbol)
    if info is None:
        raise SymbolVerificationError(
            f"Configured symbol '{symbol}' not found in MT5 terminal"
        )

    if not bool(getattr(info, "visible", False)):
        if not client.symbol_select(symbol, True):
            raise SymbolVerificationError(
                f"Failed to enable symbol '{symbol}' in MT5 Market Watch"
            )
        info = client.symbol_info(symbol)
        if info is None:
            raise SymbolVerificationError(
                f"Symbol '{symbol}' disappeared after symbol_select()"
            )

    trade_mode = int(getattr(info, "trade_mode", -1))
    if trade_mode != SYMBOL_TRADE_MODE_FULL:
        raise SymbolVerificationError(
            f"Symbol '{symbol}' is not fully tradeable (trade_mode={trade_mode})"
        )

    volume_min = Decimal(str(getattr(info, "volume_min", 0)))
    volume_max = Decimal(str(getattr(info, "volume_max", 0)))
    volume_step = Decimal(str(getattr(info, "volume_step", 0)))
    trade_contract_size = Decimal(str(getattr(info, "trade_contract_size", 0)))
    trade_tick_size = Decimal(str(getattr(info, "trade_tick_size", 0)))
    trade_tick_value = Decimal(str(getattr(info, "trade_tick_value", 0)))
    point = Decimal(str(getattr(info, "point", 0)))
    digits = int(getattr(info, "digits", -1))
    trade_stops_level = int(getattr(info, "trade_stops_level", -1))
    trade_freeze_level = int(getattr(info, "trade_freeze_level", -1))
    filling_mode_flags = int(getattr(info, "filling_mode", -1))
    expiration_mode_flags = int(getattr(info, "expiration_mode", 0))

    positive_checks = {
        "volume_min": volume_min,
        "volume_max": volume_max,
        "volume_step": volume_step,
        "trade_contract_size": trade_contract_size,
        "trade_tick_size": trade_tick_size,
        "trade_tick_value": trade_tick_value,
        "point": point,
    }
    for name, value in positive_checks.items():
        if value <= 0:
            raise SymbolVerificationError(
                f"Symbol '{symbol}' has invalid {name}={value} (must be > 0)"
            )

    if volume_min > volume_max:
        raise SymbolVerificationError(
            f"Symbol '{symbol}' volume_min ({volume_min}) > volume_max ({volume_max})"
        )
    if digits < 0 or trade_stops_level < 0 or trade_freeze_level < 0:
        raise SymbolVerificationError(
            f"Symbol '{symbol}' has negative digits/stops/freeze metadata"
        )

    filling_mode = resolve_filling_mode(filling_mode_flags)

    return VerifiedSymbolSpecs(
        symbol=symbol,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
        trade_contract_size=trade_contract_size,
        trade_tick_size=trade_tick_size,
        trade_tick_value=trade_tick_value,
        point=point,
        digits=digits,
        trade_stops_level=trade_stops_level,
        trade_freeze_level=trade_freeze_level,
        filling_mode=filling_mode,
        filling_mode_flags=filling_mode_flags,
        expiration_mode_flags=expiration_mode_flags,
        trade_mode=trade_mode,
    )
