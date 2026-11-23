"""MT5 account and terminal verification (Standard & Standard Cent support)."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.config import WorkerConfig
from app.mt5.client import MT5Client

# Official MetaTrader5 enum integer values
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_CONTEST = 1
ACCOUNT_TRADE_MODE_REAL = 2

ACCOUNT_MARGIN_MODE_RETAIL_NETTING = 0
ACCOUNT_MARGIN_MODE_EXCHANGE = 1
ACCOUNT_MARGIN_MODE_RETAIL_HEDGING = 2

CENT_CURRENCIES = {"USC", "EURC", "GBPC"}


class AccountVerificationError(RuntimeError):
    """Raised when the connected MT5 account fails mandatory safety verification."""


class VerifiedAccount(BaseModel):
    login: str
    server: str
    account_currency: str
    currency_scale: int
    account_type: Literal["STANDARD", "CENT"]
    margin_mode: Literal["HEDGING", "NETTING", "EXCHANGE"]
    mode: Literal["DEMO", "LIVE"]
    balance: Decimal
    equity: Decimal
    free_margin: Decimal
    terminal_connected: bool
    terminal_trade_allowed: bool
    account_trade_allowed: bool


def _map_trade_mode(raw_mode: int) -> Literal["DEMO", "LIVE"]:
    if raw_mode == ACCOUNT_TRADE_MODE_DEMO:
        return "DEMO"
    if raw_mode == ACCOUNT_TRADE_MODE_REAL:
        return "LIVE"
    raise AccountVerificationError(f"Unsupported MT5 account trade_mode: {raw_mode}")


def _map_margin_mode(raw_mode: int) -> Literal["HEDGING", "NETTING", "EXCHANGE"]:
    if raw_mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING:
        return "HEDGING"
    if raw_mode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING:
        return "NETTING"
    if raw_mode == ACCOUNT_MARGIN_MODE_EXCHANGE:
        return "EXCHANGE"
    raise AccountVerificationError(f"Unsupported MT5 account margin_mode: {raw_mode}")


def _infer_currency_scale(
    currency: str, configured_type: Literal["STANDARD", "CENT"]
) -> tuple[Literal["STANDARD", "CENT"], int]:
    upper = currency.strip().upper()
    is_cent_currency = upper in CENT_CURRENCIES

    if configured_type == "CENT":
        if not is_cent_currency:
            raise AccountVerificationError(
                f"Configured CENT account but MT5 currency is '{upper}' "
                "(expected USC/cent currency)"
            )
        return "CENT", 100

    if is_cent_currency:
        raise AccountVerificationError(
            f"Configured STANDARD account but MT5 currency is cent currency '{upper}'"
        )
    return "STANDARD", 1


def verify_account(
    client: MT5Client,
    config: WorkerConfig,
    *,
    require_trade_allowed: bool = False,
) -> VerifiedAccount:
    """Verify connected MT5 terminal & account match strict configuration rules."""
    term = client.terminal_info()
    if term is None:
        raise AccountVerificationError("Unable to read MT5 terminal_info()")

    terminal_connected = bool(getattr(term, "connected", False))
    if not terminal_connected:
        raise AccountVerificationError("MT5 terminal is not connected to broker server")

    terminal_trade_allowed = bool(getattr(term, "trade_allowed", False))

    acc = client.account_info()
    if acc is None:
        raise AccountVerificationError("Unable to read MT5 account_info()")

    actual_login = int(getattr(acc, "login", 0))
    actual_server = str(getattr(acc, "server", "")).strip()
    if actual_login != config.mt5_account_login:
        raise AccountVerificationError(
            f"Account login mismatch: expected {config.mt5_account_login}, got {actual_login}"
        )
    if actual_server != config.mt5_account_server:
        raise AccountVerificationError(
            f"Account server mismatch: expected '{config.mt5_account_server}', "
            f"got '{actual_server}'"
        )

    actual_mode = _map_trade_mode(int(getattr(acc, "trade_mode", -1)))
    if actual_mode != config.mt5_account_mode:
        raise AccountVerificationError(
            f"Account mode mismatch (fail-closed): configured {config.mt5_account_mode}, "
            f"MT5 is {actual_mode}"
        )

    currency = str(getattr(acc, "currency", "")).strip().upper()
    if not currency:
        raise AccountVerificationError("MT5 account currency is empty")

    account_type, currency_scale = _infer_currency_scale(
        currency, config.mt5_account_type
    )
    margin_mode = _map_margin_mode(int(getattr(acc, "margin_mode", -1)))
    account_trade_allowed = bool(getattr(acc, "trade_allowed", False))

    if require_trade_allowed:
        if not terminal_trade_allowed:
            raise AccountVerificationError(
                "MT5 terminal Algo Trading is disabled (terminal_info().trade_allowed=False)"
            )
        if not account_trade_allowed:
            raise AccountVerificationError(
                "MT5 account trading is disabled (account_info().trade_allowed=False)"
            )

    return VerifiedAccount(
        login=str(actual_login),
        server=actual_server,
        account_currency=currency,
        currency_scale=currency_scale,
        account_type=account_type,
        margin_mode=margin_mode,
        mode=actual_mode,
        balance=Decimal(str(getattr(acc, "balance", 0))),
        equity=Decimal(str(getattr(acc, "equity", 0))),
        free_margin=Decimal(str(getattr(acc, "margin_free", 0))),
        terminal_connected=terminal_connected,
        terminal_trade_allowed=terminal_trade_allowed,
        account_trade_allowed=account_trade_allowed,
    )


def verify_live_dual_lock(
    *,
    account: VerifiedAccount,
    config: WorkerConfig,
    db_account: dict[str, object] | None,
) -> None:
    """Enforce 3-way Live Mode Dual-Lock (Architecture Section 13 & Phase 10).

    Trading on a LIVE account is permitted ONLY when ALL THREE match:
      1. Worker env MT5_ACCOUNT_MODE == 'LIVE'
      2. Connected MT5 account_info().trade_mode == REAL (account.mode == 'LIVE')
      3. DB BrokerAccount has mode == 'LIVE' with non-null liveUnlockedAt & liveUnlockedBy

    Any mismatch fails closed immediately.
    """
    env_mode = config.mt5_account_mode
    mt5_mode = account.mode
    db_mode = str(db_account.get("mode", "DEMO")) if db_account else "DEMO"
    unlocked_at = db_account.get("liveUnlockedAt") if db_account else None
    unlocked_by = db_account.get("liveUnlockedBy") if db_account else None

    if env_mode != mt5_mode:
        raise AccountVerificationError(
            f"Live Dual-Lock violation: env MT5_ACCOUNT_MODE={env_mode} "
            f"but MT5 terminal is {mt5_mode}"
        )

    if env_mode == "DEMO":
        if db_mode == "LIVE":
            raise AccountVerificationError(
                "Live Dual-Lock violation: DB BrokerAccount is LIVE while worker env is DEMO"
            )
        return

    # env_mode == "LIVE" and mt5_mode == "LIVE" -> require explicit audited DB unlock
    if not db_account or db_mode != "LIVE" or not unlocked_at or not unlocked_by:
        raise AccountVerificationError(
            "Live Dual-Lock violation: LIVE mode requires explicit DB unlock "
            "(mode=LIVE, liveUnlockedAt, liveUnlockedBy)"
        )
