"""Phase 9 automated failure-drill suite verifying all critical fail-closed scenarios."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx

from app.ai.client import AiAnalyzer
from app.api_client import WorkerApiClient
from app.mt5.account import verify_account
from app.mt5.client import MT5Client
from app.mt5.execution import (
    ORDER_TYPE_BUY,
    execute_approved_proposal,
    execute_manual_close,
    proposal_comment,
    reconcile_stuck_states,
)
from app.queue.locks import KILL_SWITCH_KEY, ExecutionLockManager
from app.queue.streams import ClosePositionCommand
from tests.test_ai import FakeOpenAIClient, _approved_risk, _candidate
from tests.test_phase7_execution import FakeMT5Execution, FakeRedis, _cfg, _open_cmd


def _mock_runtime_handler(
    posted_results: list[dict[str, Any]],
    *,
    stuck_proposals: list[dict[str, Any]] | None = None,
    stuck_positions: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/runtime-state":
            return httpx.Response(
                200,
                json={
                    "killSwitchActive": False,
                    "isPaused": False,
                    "account": {"id": "acc_1"},
                    "settings": {
                        "riskPercent": "0.50",
                        "minRewardRisk": "2.00",
                        "maxSpread": "0.50",
                        "maxDeviationPoints": 30,
                        "maxPositions": 1,
                    },
                    "blackouts": [],
                },
            )
        if req.url.path.endswith("/claim"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "proposal": {
                        "id": "prop_drill_1",
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
        if req.url.path == "/api/internal/v1/reconciliation/stuck":
            return httpx.Response(
                200,
                json={
                    "stuckProposals": stuck_proposals or [],
                    "stuckPositions": stuck_positions or [],
                },
            )
        if req.url.path == "/api/internal/v1/orders/result":
            posted_results.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "orderId": "ord_drill"})
        if req.url.path == "/api/internal/v1/events":
            if events is not None:
                events.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(201, json={"id": "evt_1"})
        return httpx.Response(200, json={"ok": True})

    return handler


def test_drill_1_mt5_disconnect_fails_closed_and_reconnects() -> None:
    fake_mt5 = FakeMT5Execution(connected=False)
    client = MT5Client(mt5_module=fake_mt5)
    locks = ExecutionLockManager("redis://unused", redis_client=FakeRedis())
    posted: list[dict[str, Any]] = []

    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(_mock_runtime_handler(posted)),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=_open_cmd("prop_drill_1"),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )
    assert outcome.ok is False
    assert outcome.status == "EXECUTION_FAILED"
    assert len(fake_mt5.sent_requests) == 0

    # Once terminal recovers, ensure_connected + verify_account succeeds cleanly
    fake_mt5.connected = True
    assert client.ensure_connected() is True
    acc = verify_account(client, _cfg(), require_trade_allowed=True)
    assert acc.terminal_connected is True


def test_drill_2_algo_trading_disabled_rejects_before_order_send() -> None:
    fake_mt5 = FakeMT5Execution(connected=True)
    fake_mt5.terminal_info = lambda: SimpleNamespace(  # type: ignore[method-assign]
        connected=True, trade_allowed=False
    )
    client = MT5Client(mt5_module=fake_mt5)
    locks = ExecutionLockManager("redis://unused", redis_client=FakeRedis())
    posted: list[dict[str, Any]] = []

    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(_mock_runtime_handler(posted)),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=_open_cmd("prop_drill_1"),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )
    assert outcome.ok is False
    assert outcome.reason is not None and "Algo Trading" in outcome.reason
    assert len(fake_mt5.sent_requests) == 0


def test_drill_3_order_send_timeout_reconciles_fill_without_duplicate() -> None:
    # order_send raises TimeoutError AFTER broker already created the position
    filled_pos = SimpleNamespace(
        ticket=912345,
        magic=260101,
        comment=proposal_comment("prop_drill_1"),
        price_open=2650.22,
        volume=0.09,
    )
    fake_mt5 = FakeMT5Execution(
        positions=[filled_pos],
        send_exception=TimeoutError("MT5 IPC timeout"),
    )
    # Initially 0 positions before order_send, then filled_pos appears on reconciliation call
    call_idx = {"n": 0}
    orig_positions_get = fake_mt5.positions_get

    def staged_positions_get(**kwargs: Any) -> list[Any]:
        call_idx["n"] += 1
        if call_idx["n"] == 1:
            return []  # Pre-trade open position count = 0
        return orig_positions_get(**kwargs)

    fake_mt5.positions_get = staged_positions_get  # type: ignore[method-assign]

    client = MT5Client(mt5_module=fake_mt5)
    locks = ExecutionLockManager("redis://unused", redis_client=FakeRedis())
    posted: list[dict[str, Any]] = []

    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(_mock_runtime_handler(posted)),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=_open_cmd("prop_drill_1"),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )
    assert outcome.ok is True
    assert outcome.status == "EXECUTED"
    assert outcome.broker_position_id == "912345"
    assert len(fake_mt5.sent_requests) == 1  # Never retried!


def test_drill_4_kill_switch_blocks_open_and_allows_manual_close() -> None:
    fake_redis = FakeRedis()
    fake_redis.set(KILL_SWITCH_KEY, "1")
    locks = ExecutionLockManager("redis://unused", redis_client=fake_redis)
    open_pos = SimpleNamespace(
        ticket=555777,
        symbol="XAUUSDc",
        type=ORDER_TYPE_BUY,
        volume=0.09,
        profit=15.0,
        magic=260101,
    )
    fake_mt5 = FakeMT5Execution(positions=[open_pos])
    client = MT5Client(mt5_module=fake_mt5)
    posted: list[dict[str, Any]] = []

    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(_mock_runtime_handler(posted)),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        open_out = execute_approved_proposal(
            cmd=_open_cmd("prop_drill_1"),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
        )
        assert open_out.status == "ABORTED_KILL_SWITCH"

        close_out = execute_manual_close(
            cmd=ClosePositionCommand(
                type="CLOSE_POSITION",
                commandId="cmd-drill-close",
                idempotencyKey="idem-drill-close",
                createdAt=datetime.now(UTC).isoformat(),
                payload={
                    "positionId": "pos_555",
                    "brokerPositionId": "555777",
                    "accountLogin": "12345678",
                    "symbol": "XAUUSDc",
                    "requestedByUserId": "usr_owner_1",
                },
            ),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
        )
        assert close_out.ok is True
        assert close_out.status == "CLOSED"


def test_drill_5_ai_outage_falls_back_to_deterministic_proposal() -> None:
    fake_ai = FakeOpenAIClient(raise_exc=RuntimeError("503 OpenAI unavailable"))
    analyzer = AiAnalyzer(
        api_key="test", fallback_mode="DETERMINISTIC_ONLY", openai_client=fake_ai
    )
    out = analyzer.analyze_candidate(candidate=_candidate(), risk_result=_approved_risk())
    assert out.ai_status == "UNAVAILABLE"
    assert out.should_create_proposal is True
    assert out.target_proposal_status == "PENDING_APPROVAL"
    assert "Deterministic Summary" in out.rationale


def test_drill_6_concurrent_execution_lock_prevents_double_order() -> None:
    fake_redis = FakeRedis()
    locks = ExecutionLockManager("redis://unused", redis_client=fake_redis)
    held_token = locks.acquire("12345678", ttl_seconds=30)
    assert held_token is not None

    fake_mt5 = FakeMT5Execution()
    client = MT5Client(mt5_module=fake_mt5)
    posted: list[dict[str, Any]] = []

    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(_mock_runtime_handler(posted)),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        out = execute_approved_proposal(
            cmd=_open_cmd("prop_drill_1"),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
        )
    assert out.ok is False
    assert out.status == "LOCK_BUSY"
    assert len(fake_mt5.sent_requests) == 0
    # Releasing with wrong token must not delete held lock
    assert locks.release("12345678", "wrong-token") is False
    assert locks.release("12345678", held_token) is True


def test_drill_7_stuck_closing_reverts_when_open_and_fails_closed_when_disconnected() -> None:
    # Case A: Position still open on connected MT5 -> revert CLOSING -> OPEN
    still_open = SimpleNamespace(ticket=333444, volume=0.09)
    fake_mt5 = FakeMT5Execution(connected=True, positions=[still_open])
    posted_a: list[dict[str, Any]] = []
    stuck_pos = [
        {
            "id": "pos_stuck_1",
            "brokerAccountId": "acc_1",
            "brokerPositionId": "333444",
            "symbol": "XAUUSDc",
            "side": "BUY",
            "volume": str(Decimal("0.09")),
        }
    ]
    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(
            _mock_runtime_handler(posted_a, stuck_positions=stuck_pos)
        ),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        summary_a = reconcile_stuck_states(
            client=MT5Client(mt5_module=fake_mt5), api=api, config=_cfg()
        )
    assert summary_a["reconciledPositions"] == 1
    assert posted_a[0]["positionStatus"] == "OPEN"

    # Case B: MT5 disconnected -> mark UNKNOWN & emit CRITICAL event (fail closed)
    fake_down = FakeMT5Execution(connected=False)
    posted_b: list[dict[str, Any]] = []
    events_b: list[dict[str, Any]] = []
    stuck_prop = [
        {
            "id": "prop_stuck_down",
            "brokerAccountId": "acc_1",
            "symbol": "XAUUSDc",
            "direction": "BUY",
            "volume": "0.09",
        }
    ]
    with httpx.Client(
        base_url="http://127.0.0.1:5000",
        transport=httpx.MockTransport(
            _mock_runtime_handler(
                posted_b, stuck_proposals=stuck_prop, events=events_b
            )
        ),
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        summary_b = reconcile_stuck_states(
            client=MT5Client(mt5_module=fake_down), api=api, config=_cfg()
        )
    assert summary_b["unknownCount"] == 1
    assert posted_b[0]["orderStatus"] == "UNKNOWN"
    assert events_b[0]["severity"] == "CRITICAL"
