"""Phase 1 tests: MT5Client, Standard/Cent verification, filling_mode, UTC, API client."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.monitoring.logging import redact_mapping
from app.mt5.account import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
    ACCOUNT_TRADE_MODE_DEMO,
    ACCOUNT_TRADE_MODE_REAL,
    AccountVerificationError,
    verify_account,
)
from app.mt5.client import MT5Client, MT5ConnectionError
from app.mt5.market_data import (
    detect_server_time_offset_seconds,
    mt5_epoch_to_utc,
)
from app.mt5.symbols import (
    SYMBOL_FILLING_FOK,
    SYMBOL_FILLING_IOC,
    SYMBOL_TRADE_MODE_FULL,
    SymbolVerificationError,
    resolve_filling_mode,
    verify_symbol,
)


class FakeMT5:
    def __init__(
        self,
        *,
        init_ok: bool = True,
        terminal: Any = None,
        account: Any = None,
        symbols: dict[str, Any] | None = None,
    ) -> None:
        self.init_ok = init_ok
        self.terminal = terminal or SimpleNamespace(connected=True, trade_allowed=True)
        self.account = account or SimpleNamespace(
            login=12345678,
            server="Exness-MT5Demo",
            currency="USC",
            trade_mode=ACCOUNT_TRADE_MODE_DEMO,
            margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            trade_allowed=True,
            balance=10000.0,
            equity=10050.0,
            margin_free=9950.0,
        )
        self.symbols = symbols if symbols is not None else {}
        self.shutdown_calls = 0

    def initialize(self, **kwargs: Any) -> bool:
        return self.init_ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def terminal_info(self) -> Any:
        return self.terminal

    def account_info(self) -> Any:
        return self.account

    def symbol_info(self, symbol: str) -> Any:
        return self.symbols.get(symbol)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        sym = self.symbols.get(symbol)
        if sym is None:
            return False
        sym.visible = enable
        return True

    def last_error(self) -> tuple[int, str]:
        return (-1, "mock_error")


def _cfg(**overrides: Any) -> WorkerConfig:
    base: dict[str, Any] = {
        "worker_auth_token": "test_worker_token_at_least_16_chars",
        "mt5_account_login": 12345678,
        "mt5_account_server": "Exness-MT5Demo",
        "mt5_account_mode": "DEMO",
        "mt5_account_type": "CENT",
        "mt5_symbol": "XAUUSDc",
    }
    base.update(overrides)
    return WorkerConfig(**base)


def test_mt5_client_raises_on_failed_initialize() -> None:
    client = MT5Client(mt5_module=FakeMT5(init_ok=False))
    with pytest.raises(MT5ConnectionError):
        client.initialize()


def test_verify_cent_demo_account_succeeds() -> None:
    client = MT5Client(mt5_module=FakeMT5())
    client.initialize()
    verified = verify_account(client, _cfg(), require_trade_allowed=True)
    assert verified.account_type == "CENT"
    assert verified.account_currency == "USC"
    assert verified.currency_scale == 100
    assert verified.mode == "DEMO"
    assert verified.margin_mode == "HEDGING"
    assert verified.balance == Decimal("10000.0")


def test_verify_standard_demo_account_succeeds() -> None:
    fake = FakeMT5(
        account=SimpleNamespace(
            login=99999,
            server="Exness-MT5Demo",
            currency="USD",
            trade_mode=ACCOUNT_TRADE_MODE_DEMO,
            margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            trade_allowed=True,
            balance=500.0,
            equity=500.0,
            margin_free=500.0,
        )
    )
    client = MT5Client(mt5_module=fake)
    client.initialize()
    verified = verify_account(
        client,
        _cfg(
            mt5_account_login=99999,
            mt5_account_type="STANDARD",
            mt5_symbol="XAUUSDm",
        ),
        require_trade_allowed=True,
    )
    assert verified.account_type == "STANDARD"
    assert verified.account_currency == "USD"
    assert verified.currency_scale == 1


def test_verify_account_rejects_live_mode_mismatch() -> None:
    fake = FakeMT5(
        account=SimpleNamespace(
            login=12345678,
            server="Exness-MT5Demo",
            currency="USC",
            trade_mode=ACCOUNT_TRADE_MODE_REAL,
            margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            trade_allowed=True,
            balance=10000.0,
            equity=10000.0,
            margin_free=10000.0,
        )
    )
    client = MT5Client(mt5_module=fake)
    client.initialize()
    with pytest.raises(AccountVerificationError, match="mode mismatch"):
        verify_account(client, _cfg(mt5_account_mode="DEMO"))


def test_verify_account_rejects_disabled_algo_trading_when_required() -> None:
    fake = FakeMT5(terminal=SimpleNamespace(connected=True, trade_allowed=False))
    client = MT5Client(mt5_module=fake)
    client.initialize()
    with pytest.raises(AccountVerificationError, match="Algo Trading"):
        verify_account(client, _cfg(), require_trade_allowed=True)


def test_resolve_filling_mode_bitmasks() -> None:
    assert resolve_filling_mode(SYMBOL_FILLING_IOC) == "IOC"
    assert resolve_filling_mode(SYMBOL_FILLING_FOK) == "FOK"
    assert resolve_filling_mode(SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC) == "IOC"
    assert resolve_filling_mode(0) == "RETURN"
    with pytest.raises(SymbolVerificationError):
        resolve_filling_mode(-1)


def test_verify_symbol_auto_selects_and_validates_specs() -> None:
    sym = SimpleNamespace(
        visible=False,
        trade_mode=SYMBOL_TRADE_MODE_FULL,
        volume_min=0.01,
        volume_max=200.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        point=0.01,
        digits=2,
        trade_stops_level=10,
        trade_freeze_level=0,
        filling_mode=SYMBOL_FILLING_IOC,
        expiration_mode=15,
    )
    client = MT5Client(mt5_module=FakeMT5(symbols={"XAUUSDc": sym}))
    specs = verify_symbol(client, "XAUUSDc")
    assert sym.visible is True
    assert specs.symbol == "XAUUSDc"
    assert specs.filling_mode == "IOC"
    assert specs.volume_min == Decimal("0.01")


def test_verify_symbol_rejects_untradeable_or_zero_specs() -> None:
    bad_sym = SimpleNamespace(
        visible=True,
        trade_mode=0,
        volume_min=0.01,
        volume_max=200.0,
        volume_step=0.01,
        trade_contract_size=100.0,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        point=0.01,
        digits=2,
        trade_stops_level=0,
        trade_freeze_level=0,
        filling_mode=SYMBOL_FILLING_IOC,
    )
    client = MT5Client(mt5_module=FakeMT5(symbols={"XAUUSDc": bad_sym}))
    with pytest.raises(SymbolVerificationError, match="not fully tradeable"):
        verify_symbol(client, "XAUUSDc")


def test_server_time_offset_and_utc_normalization() -> None:
    now_utc = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)
    # Simulate broker server running at UTC+2 (+7200s) with 3 seconds of latency
    tick_epoch = int(now_utc.timestamp()) + 7200 - 3
    offset = detect_server_time_offset_seconds(tick_epoch, now_utc=now_utc)
    assert offset == 7200

    candle_mt5_epoch = int(datetime(2026, 11, 23, 14, 0, 0, tzinfo=UTC).timestamp())
    normalized = mt5_epoch_to_utc(candle_mt5_epoch, offset)
    assert normalized == datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)


def test_logger_redacts_secrets_and_api_client_sends_bearer_token() -> None:
    redacted = redact_mapping(
        {
            "authorization": "Bearer secret",
            "nested": {"openai_api_key": "sk-123", "symbol": "XAUUSDc"},
        }
    )
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["openai_api_key"] == "[REDACTED]"
    assert redacted["nested"]["symbol"] == "XAUUSDc"

    seen_auth: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_auth.append(req.headers.get("Authorization", ""))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    with httpx.Client(base_url="http://127.0.0.1:5000", transport=transport) as raw:
        api = WorkerApiClient(
            base_url="http://127.0.0.1:5000",
            auth_token="my_secret_worker_token_123",
            client=raw,
        )
        assert api.ping() == {"ok": True}

    assert seen_auth == ["Bearer my_secret_worker_token_123"]
