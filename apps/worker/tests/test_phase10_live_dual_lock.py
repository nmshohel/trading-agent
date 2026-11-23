"""Phase 10 tests: every 3-way Live Mode Dual-Lock mismatch combination fails closed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.mt5.account import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
    ACCOUNT_TRADE_MODE_REAL,
    AccountVerificationError,
    VerifiedAccount,
    verify_live_dual_lock,
)
from app.mt5.client import MT5Client
from app.mt5.execution import execute_approved_proposal
from app.queue.locks import ExecutionLockManager
from tests.test_phase7_execution import FakeMT5Execution, FakeRedis, _open_cmd


def _verified(mode: str) -> VerifiedAccount:
    return VerifiedAccount(
        login="12345678",
        server="Exness-MT5Real" if mode == "LIVE" else "Exness-MT5Demo",
        account_currency="USC",
        currency_scale=100,
        account_type="CENT",
        margin_mode="HEDGING",
        mode=mode,  # type: ignore[arg-type]
        balance=Decimal("10000.00"),
        equity=Decimal("10000.00"),
        free_margin=Decimal("10000.00"),
        terminal_connected=True,
        terminal_trade_allowed=True,
        account_trade_allowed=True,
    )


def _worker_cfg(mode: str) -> WorkerConfig:
    return WorkerConfig(
        worker_auth_token="test_worker_token_at_least_16_chars",
        mt5_account_login=12345678,
        mt5_account_server="Exness-MT5Real" if mode == "LIVE" else "Exness-MT5Demo",
        mt5_account_mode=mode,  # type: ignore[arg-type]
        mt5_account_type="CENT",
        mt5_symbol="XAUUSDc",
    )


def test_dual_lock_allows_clean_demo_and_fully_unlocked_live() -> None:
    verify_live_dual_lock(
        account=_verified("DEMO"),
        config=_worker_cfg("DEMO"),
        db_account={"mode": "DEMO", "liveUnlockedAt": None, "liveUnlockedBy": None},
    )

    verify_live_dual_lock(
        account=_verified("LIVE"),
        config=_worker_cfg("LIVE"),
        db_account={
            "mode": "LIVE",
            "liveUnlockedAt": "2026-11-23T12:00:00Z",
            "liveUnlockedBy": "usr_owner_1",
        },
    )


@pytest.mark.parametrize(
    ("env_mode", "mt5_mode", "db_account"),
    [
        ("DEMO", "LIVE", {"mode": "DEMO"}),
        (
            "LIVE",
            "DEMO",
            {"mode": "LIVE", "liveUnlockedAt": "2026-11-23T12:00:00Z", "liveUnlockedBy": "u1"},
        ),
        (
            "DEMO",
            "DEMO",
            {"mode": "LIVE", "liveUnlockedAt": "2026-11-23T12:00:00Z", "liveUnlockedBy": "u1"},
        ),
        ("LIVE", "LIVE", None),
        ("LIVE", "LIVE", {"mode": "DEMO", "liveUnlockedAt": None, "liveUnlockedBy": None}),
        ("LIVE", "LIVE", {"mode": "LIVE", "liveUnlockedAt": None, "liveUnlockedBy": "u1"}),
        (
            "LIVE",
            "LIVE",
            {"mode": "LIVE", "liveUnlockedAt": "2026-11-23T12:00:00Z", "liveUnlockedBy": None},
        ),
    ],
)
def test_dual_lock_fails_closed_on_every_mismatch(
    env_mode: str, mt5_mode: str, db_account: dict[str, Any] | None
) -> None:
    with pytest.raises(AccountVerificationError, match="Dual-Lock"):
        verify_live_dual_lock(
            account=_verified(mt5_mode),
            config=_worker_cfg(env_mode),
            db_account=db_account,
        )


def test_execution_blocks_live_order_without_db_unlock_and_logs_critical_event() -> None:
    fake_mt5 = FakeMT5Execution()
    fake_mt5.account_info = lambda: SimpleNamespace(  # type: ignore[method-assign]
        login=12345678,
        server="Exness-MT5Real",
        currency="USC",
        trade_mode=ACCOUNT_TRADE_MODE_REAL,
        margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
        trade_allowed=True,
        balance=10000.0,
        equity=10000.0,
        margin_free=9800.0,
    )
    events: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/runtime-state":
            return httpx.Response(
                200,
                json={
                    "killSwitchActive": False,
                    "isPaused": False,
                    "account": {
                        "id": "acc_1",
                        "mode": "DEMO",
                        "liveUnlockedAt": None,
                        "liveUnlockedBy": None,
                    },
                    "settings": {},
                },
            )
        if req.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "proposal": {
                        "id": "prop_live_blocked",
                        "brokerAccountId": "acc_1",
                        "symbol": "XAUUSDc",
                        "direction": "BUY",
                        "entry": "2650.20",
                        "stopLoss": "2645.20",
                        "takeProfit": "2660.20",
                        "volume": "0.09",
                    },
                },
            )
        if req.url.path == "/api/internal/v1/events":
            events.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(201, json={"id": "evt_crit"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=_open_cmd("prop_live_blocked"),
            client=MT5Client(mt5_module=fake_mt5),
            api=api,
            locks=ExecutionLockManager("redis://unused", redis_client=FakeRedis()),
            config=_worker_cfg("LIVE"),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )

    assert outcome.ok is False
    assert outcome.status == "EXECUTION_FAILED"
    assert len(fake_mt5.sent_requests) == 0
    assert any(e["severity"] == "CRITICAL" for e in events)


def test_execution_blocks_auto_approved_order_even_on_fully_unlocked_live_account() -> None:
    fake_mt5 = FakeMT5Execution()
    fake_mt5.account_info = lambda: SimpleNamespace(  # type: ignore[method-assign]
        login=12345678,
        server="Exness-MT5Real",
        currency="USC",
        trade_mode=ACCOUNT_TRADE_MODE_REAL,
        margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
        trade_allowed=True,
        balance=10000.0,
        equity=10000.0,
        margin_free=9800.0,
    )
    events: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/runtime-state":
            return httpx.Response(
                200,
                json={
                    "killSwitchActive": False,
                    "isPaused": False,
                    "account": {
                        "id": "acc_1",
                        "mode": "LIVE",
                        "liveUnlockedAt": "2026-11-23T12:00:00Z",
                        "liveUnlockedBy": "usr_owner_1",
                    },
                    "settings": {},
                },
            )
        if req.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "proposal": {
                        "id": "prop_live_auto_blocked",
                        "brokerAccountId": "acc_1",
                        "symbol": "XAUUSDc",
                        "direction": "BUY",
                        "entry": "2650.20",
                        "stopLoss": "2645.20",
                        "takeProfit": "2660.20",
                        "volume": "0.09",
                        "autoApproved": True,
                    },
                },
            )
        if req.url.path == "/api/internal/v1/events":
            events.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(201, json={"id": "evt_live_auto_blocked"})
        return httpx.Response(200, json={"ok": True})

    cmd = _open_cmd("prop_live_auto_blocked")
    cmd.payload.autoApproved = True

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=cmd,
            client=MT5Client(mt5_module=fake_mt5),
            api=api,
            locks=ExecutionLockManager("redis://unused", redis_client=FakeRedis()),
            config=_worker_cfg("LIVE"),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )

    assert outcome.ok is False
    assert outcome.status == "EXECUTION_FAILED"
    assert "LIVE" in (outcome.reason or "")
    assert len(fake_mt5.sent_requests) == 0
    assert any(e["severity"] == "CRITICAL" for e in events)
