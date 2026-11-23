"""Phase 7 worker tests: locks, streams/DLQ, demo open, manual close & stuck recovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.mt5.account import (
    ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
    ACCOUNT_TRADE_MODE_DEMO,
)
from app.mt5.client import MT5Client
from app.mt5.execution import (
    ORDER_TYPE_BUY,
    TRADE_RETCODE_DONE,
    execute_approved_proposal,
    execute_manual_close,
    proposal_comment,
    reconcile_stuck_states,
)
from app.mt5.symbols import SYMBOL_FILLING_IOC, SYMBOL_TRADE_MODE_FULL
from app.queue.locks import KILL_SWITCH_KEY, ExecutionLockManager
from app.queue.streams import (
    ClosePositionCommand,
    ExecuteProposalCommand,
    TradingCommandStream,
)


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.stream_entries: list[tuple[str, dict[str, str]]] = []
        self.acked: list[str] = []
        self.dlq: list[dict[str, str]] = []

    def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.kv.get(key)

    def delete(self, key: str) -> int:
        return 1 if self.kv.pop(key, None) is not None else 0

    def xgroup_create(self, *args: Any, **kwargs: Any) -> bool:
        return True

    def xreadgroup(self, **kwargs: Any) -> list[Any]:
        if not self.stream_entries:
            return []
        batch = list(self.stream_entries)
        self.stream_entries.clear()
        return [("commands:trading", batch)]

    def xack(self, stream: str, group: str, entry_id: str) -> int:
        self.acked.append(entry_id)
        return 1

    def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.dlq.append(fields)
        return f"dlq-{len(self.dlq)}"

    def close(self) -> None:
        pass


class FakeMT5Execution:
    def __init__(
        self,
        *,
        connected: bool = True,
        bid: float = 2650.00,
        ask: float = 2650.20,
        positions: list[Any] | None = None,
        deals: list[Any] | None = None,
        send_exception: Exception | None = None,
        send_retcode: int = TRADE_RETCODE_DONE,
    ) -> None:
        self.connected = connected
        self.bid = bid
        self.ask = ask
        self.positions = positions if positions is not None else []
        self.deals = deals if deals is not None else []
        self.send_exception = send_exception
        self.send_retcode = send_retcode
        self.sent_requests: list[dict[str, Any]] = []

    def initialize(self, **kwargs: Any) -> bool:
        return self.connected

    def shutdown(self) -> None:
        pass

    def terminal_info(self) -> Any:
        return SimpleNamespace(connected=self.connected, trade_allowed=True)

    def account_info(self) -> Any:
        return SimpleNamespace(
            login=12345678,
            server="Exness-MT5Demo",
            currency="USC",
            trade_mode=ACCOUNT_TRADE_MODE_DEMO,
            margin_mode=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING,
            trade_allowed=True,
            balance=10000.0,
            equity=10000.0,
            margin_free=9800.0,
        )

    def symbol_info(self, symbol: str) -> Any:
        return SimpleNamespace(
            visible=True,
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

    def symbol_info_tick(self, symbol: str) -> Any:
        return SimpleNamespace(
            time=int(datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC).timestamp()),
            bid=self.bid,
            ask=self.ask,
        )

    def order_calc_profit(
        self, action: int, symbol: str, volume: float, price_open: float, price_close: float
    ) -> float:
        delta = (
            (price_close - price_open)
            if action == ORDER_TYPE_BUY
            else (price_open - price_close)
        )
        return round(delta * 100.0 * volume, 4)

    def order_calc_margin(self, action: int, symbol: str, volume: float, price: float) -> float:
        return round(150.0 * volume, 4)

    def order_check(self, request: dict[str, Any]) -> Any:
        return SimpleNamespace(retcode=0)

    def order_send(self, request: dict[str, Any]) -> Any:
        self.sent_requests.append(request)
        if self.send_exception is not None:
            raise self.send_exception
        return SimpleNamespace(
            retcode=self.send_retcode,
            order=500001,
            deal=600001,
            price=request["price"],
        )

    def positions_get(self, **kwargs: Any) -> list[Any]:
        ticket = kwargs.get("ticket")
        if ticket is not None:
            return [p for p in self.positions if int(p.ticket) == int(ticket)]
        return list(self.positions)

    def history_deals_get(self, *args: Any, **kwargs: Any) -> list[Any]:
        return list(self.deals)


def _cfg() -> WorkerConfig:
    return WorkerConfig(
        worker_auth_token="test_worker_token_at_least_16_chars",
        mt5_account_login=12345678,
        mt5_account_server="Exness-MT5Demo",
        mt5_account_mode="DEMO",
        mt5_account_type="CENT",
        mt5_symbol="XAUUSDc",
        mt5_magic_number=260101,
    )


def _open_cmd(proposal_id: str = "prop_abc123") -> ExecuteProposalCommand:
    return ExecuteProposalCommand(
        type="EXECUTE_PROPOSAL",
        commandId="cmd-1",
        idempotencyKey="idem-open-1",
        createdAt=datetime.now(UTC).isoformat(),
        payload={
            "proposalId": proposal_id,
            "approvalId": "appr-1",
            "accountLogin": "12345678",
            "symbol": "XAUUSDc",
        },
    )


def test_open_execution_runs_order_check_and_single_order_send() -> None:
    fake_redis = FakeRedis()
    locks = ExecutionLockManager("redis://unused", redis_client=fake_redis)
    fake_mt5 = FakeMT5Execution()
    client = MT5Client(mt5_module=fake_mt5)
    posted_results: list[dict[str, Any]] = []

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
                        "id": "prop_abc123",
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
        if req.url.path == "/api/internal/v1/orders/result":
            posted_results.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "orderId": "ord_1"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        outcome = execute_approved_proposal(
            cmd=_open_cmd(),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
            now_utc=datetime(2026, 11, 3, 13, 0, 2, tzinfo=UTC),
        )

    assert outcome.ok is True
    assert outcome.status == "EXECUTED"
    assert len(fake_mt5.sent_requests) == 1
    sent = fake_mt5.sent_requests[0]
    assert sent["magic"] == 260101
    assert sent["comment"] == proposal_comment("prop_abc123")
    assert sent["sl"] == 2645.20
    assert sent["tp"] == 2660.20
    assert len(posted_results) == 1
    assert posted_results[0]["orderStatus"] == "FILLED"
    # Lock released cleanly
    assert fake_redis.get(locks.lock_key("12345678")) is None


def test_kill_switch_aborts_open_but_manual_close_still_executes() -> None:
    fake_redis = FakeRedis()
    fake_redis.set(KILL_SWITCH_KEY, "1")  # Kill Switch ACTIVE
    locks = ExecutionLockManager("redis://unused", redis_client=fake_redis)

    open_pos = SimpleNamespace(
        ticket=99887766,
        symbol="XAUUSDc",
        type=ORDER_TYPE_BUY,
        volume=0.09,
        profit=32.5,
        magic=260101,
    )
    closing_deal = SimpleNamespace(
        ticket=777001,
        price=2653.80,
        profit=32.40,
        commission=-0.63,
        swap=0.0,
    )
    fake_mt5 = FakeMT5Execution(positions=[open_pos], deals=[closing_deal])
    client = MT5Client(mt5_module=fake_mt5)
    posted_results: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/runtime-state":
            return httpx.Response(
                200, json={"killSwitchActive": True, "account": {"id": "acc_1"}, "settings": {}}
            )
        if req.url.path == "/api/internal/v1/orders/result":
            posted_results.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "orderId": "ord_close_1"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)

        # 1. Open MUST abort immediately before touching MT5
        open_res = execute_approved_proposal(
            cmd=_open_cmd(),
            client=client,
            api=api,
            locks=locks,
            config=_cfg(),
        )
        assert open_res.ok is False
        assert open_res.status == "ABORTED_KILL_SWITCH"
        assert len(fake_mt5.sent_requests) == 0

        # 2. Manual close MUST STILL SUCCEED despite Kill Switch ON
        close_cmd = ClosePositionCommand(
            type="CLOSE_POSITION",
            commandId="cmd-close-1",
            idempotencyKey="idem-close-1",
            createdAt=datetime.now(UTC).isoformat(),
            payload={
                "positionId": "pos_1",
                "brokerPositionId": "99887766",
                "accountLogin": "12345678",
                "symbol": "XAUUSDc",
                "requestedByUserId": "usr_owner_1",
            },
        )
        close_res = execute_manual_close(
            cmd=close_cmd, client=client, api=api, locks=locks, config=_cfg()
        )
        assert close_res.ok is True
        assert close_res.status == "CLOSED"
        assert len(fake_mt5.sent_requests) == 1
        assert fake_mt5.sent_requests[0]["position"] == 99887766
        assert posted_results[-1]["positionStatus"] == "CLOSED"
        assert posted_results[-1]["realizedProfit"] == "32.4"


def test_stuck_executing_recovery_reconciles_existing_mt5_fill_without_duplicate() -> None:
    # Simulate worker crashed after order_send; position already exists on MT5 with magic + comment
    matched_pos = SimpleNamespace(
        ticket=888111,
        magic=260101,
        comment=proposal_comment("prop_stuck_1"),
        price_open=2650.25,
        volume=0.09,
    )
    fake_mt5 = FakeMT5Execution(positions=[matched_pos])
    client = MT5Client(mt5_module=fake_mt5)
    posted: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/reconciliation/stuck":
            return httpx.Response(
                200,
                json={
                    "stuckProposals": [
                        {
                            "id": "prop_stuck_1",
                            "brokerAccountId": "acc_1",
                            "symbol": "XAUUSDc",
                            "direction": "BUY",
                            "volume": "0.09",
                        }
                    ],
                    "stuckPositions": [],
                },
            )
        if req.url.path == "/api/internal/v1/orders/result":
            posted.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(200, json={"ok": True, "orderId": "ord_rec_1"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        summary = reconcile_stuck_states(client=client, api=api, config=_cfg())

    assert summary["reconciledProposals"] == 1
    assert len(fake_mt5.sent_requests) == 0  # Zero duplicate order_send!
    assert posted[0]["proposalStatus"] == "EXECUTED"
    assert posted[0]["brokerPositionId"] == "888111"


def test_redis_stream_routes_poison_messages_to_dlq() -> None:
    fake_redis = FakeRedis()
    fake_redis.stream_entries.append(("1-0", {"payload": "{not-valid-json"}))
    stream = TradingCommandStream("redis://unused", redis_client=fake_redis)
    cmds = stream.read_commands()
    assert cmds == []
    assert len(fake_redis.dlq) == 1
    assert fake_redis.acked == ["1-0"]
