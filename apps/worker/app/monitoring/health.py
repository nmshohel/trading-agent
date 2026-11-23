"""Worker heartbeat & account sync reporter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.mt5.account import VerifiedAccount
from app.mt5.symbols import VerifiedSymbolSpecs


def build_heartbeat_payload(
    *,
    worker_id: str,
    account: VerifiedAccount | None,
    symbol: str,
    server_time_offset_seconds: int | None,
    mt5_connected: bool,
) -> dict[str, Any]:
    return {
        "workerId": worker_id,
        "mt5Connected": mt5_connected,
        "terminalTradeAllowed": account.terminal_trade_allowed if account else False,
        "accountTradeAllowed": account.account_trade_allowed if account else False,
        "accountLogin": account.login if account else None,
        "accountServer": account.server if account else None,
        "accountMode": account.mode if account else None,
        "symbol": symbol,
        "serverTimeOffsetSeconds": server_time_offset_seconds,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }


def build_account_sync_payload(
    *,
    config: WorkerConfig,
    account: VerifiedAccount,
    symbol_specs: VerifiedSymbolSpecs,
) -> dict[str, Any]:
    return {
        "broker": "Exness",
        "platform": "MT5",
        "login": account.login,
        "server": account.server,
        "accountCurrency": account.account_currency,
        "currencyScale": account.currency_scale,
        "accountType": account.account_type,
        "marginMode": account.margin_mode,
        "magicNumber": config.mt5_magic_number,
        "mode": account.mode,
        "balance": str(account.balance),
        "equity": str(account.equity),
        "freeMargin": str(account.free_margin),
        "symbolSpecs": symbol_specs.to_api_payload(),
    }


def sync_and_heartbeat(
    *,
    api: WorkerApiClient,
    config: WorkerConfig,
    account: VerifiedAccount,
    symbol_specs: VerifiedSymbolSpecs,
    server_time_offset_seconds: int,
    worker_id: str = "win-mt5-1",
) -> None:
    api.sync_account(
        build_account_sync_payload(
            config=config, account=account, symbol_specs=symbol_specs
        )
    )
    api.send_heartbeat(
        build_heartbeat_payload(
            worker_id=worker_id,
            account=account,
            symbol=symbol_specs.symbol,
            server_time_offset_seconds=server_time_offset_seconds,
            mt5_connected=account.terminal_connected,
        )
    )
