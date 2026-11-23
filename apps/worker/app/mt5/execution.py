"""MT5 order_check, order_send (open & manual close), and stuck-state reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.market.features import parse_blackouts
from app.mt5.account import (
    AccountVerificationError,
    verify_account,
    verify_live_dual_lock,
)
from app.mt5.client import MT5Client, MT5ConnectionError
from app.mt5.market_data import detect_live_server_offset, fetch_live_tick
from app.mt5.symbols import SymbolVerificationError, verify_symbol
from app.queue.locks import ExecutionLockManager
from app.queue.streams import ClosePositionCommand, ExecuteProposalCommand
from app.risk.limits import RiskSnapshotState
from app.risk.validator import PreTradeRiskContext, validate_pre_trade

# Official MetaTrader5 trading constants
TRADE_ACTION_DEAL = 1
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
ORDER_TIME_GTC = 0
ORDER_FILLING_FOK = 0
ORDER_FILLING_IOC = 1
ORDER_FILLING_RETURN = 2
TRADE_RETCODE_DONE = 10009
TRADE_RETCODE_DONE_PARTIAL = 10010

FILLING_MODE_TO_MT5: dict[str, int] = {
    "FOK": ORDER_FILLING_FOK,
    "IOC": ORDER_FILLING_IOC,
    "RETURN": ORDER_FILLING_RETURN,
}


class ExecutionOutcome(BaseModel):
    ok: bool
    status: str
    retcode: int | None = None
    broker_order_id: str | None = None
    broker_deal_id: str | None = None
    broker_position_id: str | None = None
    reason: str | None = None


def proposal_comment(proposal_id: str) -> str:
    """Deterministic short MT5 order comment (<= 31 chars) for reconciliation."""
    return f"AGY:{proposal_id[:20]}"


def close_comment(position_id: str) -> str:
    return f"AGY:CL:{position_id[:16]}"


def _find_matching_mt5_fill(
    client: MT5Client,
    *,
    magic: int,
    comment: str,
    symbol: str,
) -> dict[str, Any] | None:
    """Inspect MT5 open positions & recent deal history by magic + comment."""
    positions = client.positions_get(symbol=symbol) or []
    for pos in positions:
        if (
            int(getattr(pos, "magic", -1)) == magic
            and str(getattr(pos, "comment", "")) == comment
        ):
            return {
                "brokerPositionId": str(pos.ticket),
                "entryPrice": str(getattr(pos, "price_open", 0)),
                "volume": str(getattr(pos, "volume", 0)),
            }

    now = datetime.now(UTC)
    deals = client.history_deals_get(now - timedelta(hours=2), now + timedelta(minutes=5)) or []
    for deal in deals:
        if (
            int(getattr(deal, "magic", -1)) == magic
            and str(getattr(deal, "comment", "")) == comment
        ):
            return {
                "brokerOrderId": str(getattr(deal, "order", "")),
                "brokerDealId": str(getattr(deal, "ticket", "")),
                "brokerPositionId": str(getattr(deal, "position_id", "")),
                "entryPrice": str(getattr(deal, "price", 0)),
                "volume": str(getattr(deal, "volume", 0)),
            }
    return None


def _build_risk_snapshot(raw: dict[str, Any] | None) -> RiskSnapshotState | None:
    if not raw:
        return None
    reset_raw = raw.get("resetAt")
    reset_at = (
        datetime.fromisoformat(str(reset_raw).replace("Z", "+00:00"))
        if reset_raw
        else None
    )
    return RiskSnapshotState(
        starting_equity=Decimal(str(raw["startingEquity"])),
        loss_limit_percent=Decimal(str(raw["lossLimit"])),
        is_tripped=bool(raw.get("isTripped", False)),
        reset_at=reset_at,
    )


def execute_approved_proposal(
    *,
    cmd: ExecuteProposalCommand,
    client: MT5Client,
    api: WorkerApiClient,
    locks: ExecutionLockManager,
    config: WorkerConfig,
    now_utc: datetime | None = None,
) -> ExecutionOutcome:
    """Execute one approved proposal following all 12 safety steps (Section 11)."""
    now = now_utc or datetime.now(UTC)
    lock_token = locks.acquire(cmd.payload.accountLogin, ttl_seconds=30)
    if lock_token is None:
        return ExecutionOutcome(
            ok=False, status="LOCK_BUSY", reason="Execution lock already held"
        )

    try:
        # Step 3: Fast kill-switch & runtime-state check before claiming
        if locks.is_kill_switch_active():
            return ExecutionOutcome(
                ok=False,
                status="ABORTED_KILL_SWITCH",
                reason="Redis kill switch is active",
            )

        runtime = api.get_runtime_state()
        if runtime.get("killSwitchActive") or runtime.get("isPaused"):
            return ExecutionOutcome(
                ok=False,
                status="ABORTED_PAUSED",
                reason="System is paused or kill switch active",
            )

        # Step 4: Atomic claim APPROVED -> EXECUTING
        try:
            claim_res = api.claim_proposal(cmd.payload.proposalId)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                return ExecutionOutcome(
                    ok=False,
                    status="CLAIM_CONFLICT",
                    reason="Proposal expired, cancelled, or already claimed",
                )
            raise

        proposal = claim_res["proposal"]
        broker_account_id = str(
            (runtime.get("account") or {}).get("id")
            or proposal.get("brokerAccountId")
            or "unknown"
        )
        direction: Literal["BUY", "SELL"] = proposal["direction"]
        comment = proposal_comment(cmd.payload.proposalId)

        def _fail_execution(reason: str, retcode: int | None = None) -> ExecutionOutcome:
            api.post_order_result(
                {
                    "proposalId": cmd.payload.proposalId,
                    "brokerAccountId": broker_account_id,
                    "actionType": "OPEN",
                    "idempotencyKey": cmd.idempotencyKey,
                    "symbol": proposal["symbol"],
                    "side": direction,
                    "volume": str(proposal["volume"]),
                    "requestedPrice": str(proposal["entry"]),
                    "stopLoss": str(proposal["stopLoss"]),
                    "takeProfit": str(proposal["takeProfit"]),
                    "retcode": retcode,
                    "brokerComment": comment,
                    "orderStatus": "REJECTED",
                    "proposalStatus": "EXECUTION_FAILED",
                    "failureReason": reason,
                    "eventPayload": {"reason": reason, "retcode": retcode},
                }
            )
            return ExecutionOutcome(
                ok=False, status="EXECUTION_FAILED", retcode=retcode, reason=reason
            )

        # Step 5: Verify MT5 connection, Algo Trading, account identity & 3-way Live Dual-Lock
        try:
            client.ensure_connected()
            account = verify_account(client, config, require_trade_allowed=True)
            verify_live_dual_lock(
                account=account,
                config=config,
                db_account=runtime.get("account"),
            )
            is_auto_approved = bool(
                cmd.payload.autoApproved or proposal.get("autoApproved")
            )
            db_account = runtime.get("account") or {}
            if is_auto_approved and (
                account.mode != "DEMO"
                or config.mt5_account_mode != "DEMO"
                or db_account.get("mode") != "DEMO"
                or db_account.get("liveUnlockedAt") is not None
            ):
                msg = (
                    "LIVE Safety Lock: auto-approved order rejected because "
                    "manual human approval is strictly required for LIVE accounts"
                )
                api.send_event(
                    severity="CRITICAL",
                    message=msg,
                    metadata={
                        "proposalId": cmd.payload.proposalId,
                        "accountMode": account.mode,
                        "configMode": config.mt5_account_mode,
                        "dbMode": db_account.get("mode"),
                    },
                )
                return _fail_execution(msg)
            symbol_specs = verify_symbol(client, proposal["symbol"])
        except (
            MT5ConnectionError,
            AccountVerificationError,
            SymbolVerificationError,
        ) as exc:
            if "Dual-Lock" in str(exc):
                api.send_event(
                    severity="CRITICAL",
                    message=str(exc),
                    metadata={"proposalId": cmd.payload.proposalId},
                )
            return _fail_execution(f"MT5 pre-trade verification failed: {exc}")

        # Step 6: Refresh live tick & open positions
        offset_seconds = detect_live_server_offset(client, symbol_specs.symbol)
        quote = fetch_live_tick(client, symbol_specs.symbol, offset_seconds)
        if quote is None:
            return _fail_execution("Unable to fetch fresh MT5 tick before execution")

        open_positions = client.positions_get(symbol=symbol_specs.symbol) or []
        settings = runtime.get("settings") or {}

        # Step 7: Full deterministic risk & price-deviation revalidation
        risk_ctx = PreTradeRiskContext(
            account=account,
            symbol_specs=symbol_specs,
            quote=quote,
            direction=direction,
            entry_reference=Decimal(str(proposal["entry"])),
            stop_loss=Decimal(str(proposal["stopLoss"])),
            take_profit=Decimal(str(proposal["takeProfit"])),
            now_utc=now,
            kill_switch_active=locks.is_kill_switch_active(),
            is_paused=bool(runtime.get("isPaused", False)),
            risk_percent=Decimal(str(settings.get("riskPercent", "0.25"))),
            min_reward_risk=Decimal(str(settings.get("minRewardRisk", "2.00"))),
            max_spread=Decimal(str(settings.get("maxSpread", "0.50"))),
            max_deviation_points=int(settings.get("maxDeviationPoints", 30)),
            commission_per_lot=Decimal(str(settings.get("commissionPerLot", "0.00"))),
            open_positions_count=len(open_positions),
            max_open_positions=int(settings.get("maxPositions", 1)),
            check_pending_limit=False,
            blackouts=parse_blackouts(runtime.get("blackouts") or []),
            daily_risk=_build_risk_snapshot(runtime.get("dailySnapshot")),
            weekly_risk=_build_risk_snapshot(runtime.get("weeklySnapshot")),
            check_price_drift=True,
            require_trade_allowed=True,
        )
        risk_res = validate_pre_trade(client, risk_ctx)
        if not risk_res.approved:
            return _fail_execution(
                "Pre-execution revalidation failed: "
                + "; ".join(risk_res.rejection_reasons)
            )

        # Step 8: Build TRADE_ACTION_DEAL with dynamic filling_mode, magic, comment & mandatory SL
        live_price = quote.ask if direction == "BUY" else quote.bid
        deviation = int(settings.get("maxDeviationPoints", 30))
        req = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol_specs.symbol,
            "volume": float(risk_res.volume),
            "type": ORDER_TYPE_BUY if direction == "BUY" else ORDER_TYPE_SELL,
            "price": float(live_price),
            "sl": float(Decimal(str(proposal["stopLoss"]))),
            "tp": float(Decimal(str(proposal["takeProfit"]))),
            "deviation": deviation,
            "magic": config.mt5_magic_number,
            "comment": comment,
            "type_time": ORDER_TIME_GTC,
            "type_filling": FILLING_MODE_TO_MT5[symbol_specs.filling_mode],
        }

        # Step 9: order_check
        check = client.order_check(req)
        if check is None or int(getattr(check, "retcode", -1)) != 0:
            rc = None if check is None else int(getattr(check, "retcode", -1))
            return _fail_execution(
                f"MT5 order_check failed (retcode={rc})", retcode=rc
            )

        # Step 10: order_send ONCE (never blindly retry on timeout!)
        try:
            send_res = client.order_send(req)
        except Exception as exc:
            # Reconcile first before declaring failure
            found = _find_matching_mt5_fill(
                client,
                magic=config.mt5_magic_number,
                comment=comment,
                symbol=symbol_specs.symbol,
            )
            if found is not None:
                api.post_order_result(
                    {
                        "proposalId": cmd.payload.proposalId,
                        "brokerAccountId": broker_account_id,
                        "actionType": "OPEN",
                        "idempotencyKey": cmd.idempotencyKey,
                        "symbol": symbol_specs.symbol,
                        "side": direction,
                        "volume": str(risk_res.volume),
                        "requestedPrice": str(live_price),
                        "entryPrice": found["entryPrice"],
                        "stopLoss": str(proposal["stopLoss"]),
                        "takeProfit": str(proposal["takeProfit"]),
                        "fillingMode": symbol_specs.filling_mode,
                        "deviationPoints": deviation,
                        "brokerOrderId": found.get("brokerOrderId"),
                        "brokerDealId": found.get("brokerDealId"),
                        "brokerPositionId": found.get("brokerPositionId"),
                        "brokerComment": comment,
                        "orderStatus": "FILLED",
                        "proposalStatus": "EXECUTED",
                        "eventPayload": {"reconciledAfterException": str(exc)},
                    }
                )
                return ExecutionOutcome(
                    ok=True,
                    status="EXECUTED",
                    broker_position_id=found.get("brokerPositionId"),
                )

            api.post_order_result(
                {
                    "proposalId": cmd.payload.proposalId,
                    "brokerAccountId": broker_account_id,
                    "actionType": "OPEN",
                    "idempotencyKey": cmd.idempotencyKey,
                    "symbol": symbol_specs.symbol,
                    "side": direction,
                    "volume": str(risk_res.volume),
                    "requestedPrice": str(live_price),
                    "brokerComment": comment,
                    "orderStatus": "UNKNOWN",
                    "failureReason": f"Uncertain order_send timeout/error: {exc}",
                    "eventPayload": {"exception": str(exc)},
                }
            )
            return ExecutionOutcome(
                ok=False, status="UNKNOWN", reason=f"order_send exception: {exc}"
            )

        if send_res is None:
            return _fail_execution("MT5 order_send returned None")

        retcode = int(getattr(send_res, "retcode", -1))
        if retcode not in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL):
            return _fail_execution(
                f"Broker rejected order_send (retcode={retcode})", retcode=retcode
            )

        broker_order_id = str(getattr(send_res, "order", "") or "") or None
        broker_deal_id = str(getattr(send_res, "deal", "") or "") or None
        fill_price = str(getattr(send_res, "price", live_price))
        found_pos = _find_matching_mt5_fill(
            client,
            magic=config.mt5_magic_number,
            comment=comment,
            symbol=symbol_specs.symbol,
        )
        broker_position_id = (
            (found_pos or {}).get("brokerPositionId") or broker_order_id
        )

        # Step 11 & 12: Persist reconciled result
        api.post_order_result(
            {
                "proposalId": cmd.payload.proposalId,
                "brokerAccountId": broker_account_id,
                "actionType": "OPEN",
                "idempotencyKey": cmd.idempotencyKey,
                "symbol": symbol_specs.symbol,
                "side": direction,
                "volume": str(risk_res.volume),
                "requestedPrice": str(live_price),
                "entryPrice": fill_price,
                "stopLoss": str(proposal["stopLoss"]),
                "takeProfit": str(proposal["takeProfit"]),
                "fillingMode": symbol_specs.filling_mode,
                "deviationPoints": deviation,
                "retcode": retcode,
                "brokerOrderId": broker_order_id,
                "brokerDealId": broker_deal_id,
                "brokerPositionId": broker_position_id,
                "brokerComment": comment,
                "orderStatus": "FILLED",
                "proposalStatus": "EXECUTED",
                "eventPayload": {
                    "retcode": retcode,
                    "fillPrice": fill_price,
                    "volume": str(risk_res.volume),
                },
            }
        )
        return ExecutionOutcome(
            ok=True,
            status="EXECUTED",
            retcode=retcode,
            broker_order_id=broker_order_id,
            broker_deal_id=broker_deal_id,
            broker_position_id=broker_position_id,
        )
    finally:
        locks.release(cmd.payload.accountLogin, lock_token)


def execute_manual_close(
    *,
    cmd: ClosePositionCommand,
    client: MT5Client,
    api: WorkerApiClient,
    locks: ExecutionLockManager,
    config: WorkerConfig,
) -> ExecutionOutcome:
    """Close an open MT5 position (risk-reducing: allowed even during Kill Switch)."""
    lock_token = locks.acquire(cmd.payload.accountLogin, ttl_seconds=30)
    if lock_token is None:
        return ExecutionOutcome(
            ok=False, status="LOCK_BUSY", reason="Execution lock already held"
        )

    try:
        runtime = api.get_runtime_state()
        broker_account_id = str((runtime.get("account") or {}).get("id") or "unknown")
        settings = runtime.get("settings") or {}
        deviation = int(settings.get("maxDeviationPoints", 30))
        ticket = int(cmd.payload.brokerPositionId)
        comment = close_comment(cmd.payload.positionId)

        client.ensure_connected()
        verify_account(client, config, require_trade_allowed=True)
        symbol_specs = verify_symbol(client, cmd.payload.symbol)

        positions = client.positions_get(ticket=ticket) or []
        if not positions:
            # Position already closed on MT5 — reconcile from deal history
            deals = client.history_deals_get(position=ticket) or []
            closing_deal = deals[-1] if deals else None
            api.post_order_result(
                {
                    "positionId": cmd.payload.positionId,
                    "brokerAccountId": broker_account_id,
                    "actionType": "CLOSE",
                    "idempotencyKey": cmd.idempotencyKey,
                    "symbol": symbol_specs.symbol,
                    "side": "SELL",
                    "volume": str(getattr(closing_deal, "volume", "0.01")),
                    "brokerDealId": (
                        str(closing_deal.ticket) if closing_deal else None
                    ),
                    "brokerPositionId": cmd.payload.brokerPositionId,
                    "brokerComment": comment,
                    "orderStatus": "FILLED",
                    "positionStatus": "CLOSED",
                    "closePrice": (
                        str(closing_deal.price) if closing_deal else None
                    ),
                    "realizedProfit": (
                        str(getattr(closing_deal, "profit", 0)) if closing_deal else "0"
                    ),
                    "commission": (
                        str(getattr(closing_deal, "commission", 0))
                        if closing_deal
                        else "0"
                    ),
                    "swap": (
                        str(getattr(closing_deal, "swap", 0)) if closing_deal else "0"
                    ),
                    "eventPayload": {"alreadyClosedOnMt5": True},
                }
            )
            return ExecutionOutcome(
                ok=True,
                status="CLOSED",
                broker_position_id=cmd.payload.brokerPositionId,
            )

        mt5_pos = positions[0]
        pos_type = int(getattr(mt5_pos, "type", ORDER_TYPE_BUY))
        close_order_type = (
            ORDER_TYPE_SELL if pos_type == ORDER_TYPE_BUY else ORDER_TYPE_BUY
        )
        close_side: Literal["BUY", "SELL"] = (
            "SELL" if close_order_type == ORDER_TYPE_SELL else "BUY"
        )
        volume = Decimal(str(getattr(mt5_pos, "volume", 0)))

        offset_seconds = detect_live_server_offset(client, symbol_specs.symbol)
        quote = fetch_live_tick(client, symbol_specs.symbol, offset_seconds)
        if quote is None:
            return ExecutionOutcome(
                ok=False, status="CLOSE_FAILED", reason="No fresh tick for manual close"
            )

        close_price = quote.bid if close_order_type == ORDER_TYPE_SELL else quote.ask
        req = {
            "action": TRADE_ACTION_DEAL,
            "symbol": symbol_specs.symbol,
            "position": ticket,
            "volume": float(volume),
            "type": close_order_type,
            "price": float(close_price),
            "deviation": deviation,
            "magic": config.mt5_magic_number,
            "comment": comment,
            "type_time": ORDER_TIME_GTC,
            "type_filling": FILLING_MODE_TO_MT5[symbol_specs.filling_mode],
        }

        check = client.order_check(req)
        if check is None or int(getattr(check, "retcode", -1)) != 0:
            rc = None if check is None else int(getattr(check, "retcode", -1))
            api.post_order_result(
                {
                    "positionId": cmd.payload.positionId,
                    "brokerAccountId": broker_account_id,
                    "actionType": "CLOSE",
                    "idempotencyKey": cmd.idempotencyKey,
                    "symbol": symbol_specs.symbol,
                    "side": close_side,
                    "volume": str(volume),
                    "retcode": rc,
                    "brokerPositionId": cmd.payload.brokerPositionId,
                    "orderStatus": "REJECTED",
                    "positionStatus": "OPEN",
                    "failureReason": f"Manual close order_check failed (retcode={rc})",
                    "eventPayload": {"retcode": rc},
                }
            )
            return ExecutionOutcome(ok=False, status="CLOSE_FAILED", retcode=rc)

        send_res = client.order_send(req)
        retcode = -1 if send_res is None else int(getattr(send_res, "retcode", -1))
        if retcode not in (TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL):
            api.post_order_result(
                {
                    "positionId": cmd.payload.positionId,
                    "brokerAccountId": broker_account_id,
                    "actionType": "CLOSE",
                    "idempotencyKey": cmd.idempotencyKey,
                    "symbol": symbol_specs.symbol,
                    "side": close_side,
                    "volume": str(volume),
                    "retcode": retcode,
                    "brokerPositionId": cmd.payload.brokerPositionId,
                    "orderStatus": "REJECTED",
                    "positionStatus": "OPEN",
                    "failureReason": f"Manual close rejected (retcode={retcode})",
                    "eventPayload": {"retcode": retcode},
                }
            )
            return ExecutionOutcome(ok=False, status="CLOSE_FAILED", retcode=retcode)

        deals = client.history_deals_get(position=ticket) or []
        closing_deal = deals[-1] if deals else None
        fill_price = str(getattr(send_res, "price", close_price))
        realized_profit = str(
            getattr(closing_deal, "profit", getattr(mt5_pos, "profit", 0))
            if closing_deal
            else getattr(mt5_pos, "profit", 0)
        )
        commission = str(getattr(closing_deal, "commission", 0)) if closing_deal else "0"
        swap = str(getattr(closing_deal, "swap", 0)) if closing_deal else "0"

        broker_order_id = str(getattr(send_res, "order", "") or "") or None
        broker_deal_id = str(getattr(send_res, "deal", "") or "") or None

        api.post_order_result(
            {
                "positionId": cmd.payload.positionId,
                "brokerAccountId": broker_account_id,
                "actionType": "CLOSE",
                "idempotencyKey": cmd.idempotencyKey,
                "symbol": symbol_specs.symbol,
                "side": close_side,
                "volume": str(volume),
                "requestedPrice": str(close_price),
                "entryPrice": fill_price,
                "fillingMode": symbol_specs.filling_mode,
                "deviationPoints": deviation,
                "retcode": retcode,
                "brokerOrderId": broker_order_id,
                "brokerDealId": broker_deal_id,
                "brokerPositionId": cmd.payload.brokerPositionId,
                "brokerComment": comment,
                "orderStatus": "FILLED",
                "positionStatus": "CLOSED",
                "closePrice": fill_price,
                "realizedProfit": realized_profit,
                "commission": commission,
                "swap": swap,
                "eventPayload": {
                    "manualClose": True,
                    "retcode": retcode,
                    "realizedProfit": realized_profit,
                },
            }
        )
        return ExecutionOutcome(
            ok=True,
            status="CLOSED",
            retcode=retcode,
            broker_order_id=broker_order_id,
            broker_deal_id=broker_deal_id,
            broker_position_id=cmd.payload.brokerPositionId,
        )
    finally:
        locks.release(cmd.payload.accountLogin, lock_token)


def reconcile_stuck_states(
    *,
    client: MT5Client,
    api: WorkerApiClient,
    config: WorkerConfig,
    threshold_seconds: int = 45,
) -> dict[str, int]:
    """Startup & periodic recovery for stuck EXECUTING proposals / CLOSING positions."""
    stuck = api.get_stuck_reconciliation(threshold_seconds=threshold_seconds)
    stuck_proposals = stuck.get("stuckProposals") or []
    stuck_positions = stuck.get("stuckPositions") or []
    summary = {"reconciledProposals": 0, "reconciledPositions": 0, "unknownCount": 0}

    if not stuck_proposals and not stuck_positions:
        return summary

    try:
        client.ensure_connected()
        mt5_ok = True
    except MT5ConnectionError:
        mt5_ok = False

    for prop in stuck_proposals:
        pid = str(prop["id"])
        comment = proposal_comment(pid)
        idem = f"reconcile-open-{pid}"
        if not mt5_ok:
            summary["unknownCount"] += 1
            api.post_order_result(
                {
                    "proposalId": pid,
                    "brokerAccountId": str(prop["brokerAccountId"]),
                    "actionType": "OPEN",
                    "idempotencyKey": idem,
                    "symbol": str(prop["symbol"]),
                    "side": prop["direction"],
                    "volume": str(prop["volume"]),
                    "brokerComment": comment,
                    "orderStatus": "UNKNOWN",
                    "failureReason": "MT5 disconnected during stuck-state recovery",
                    "eventPayload": {"reconciliation": "disconnected"},
                }
            )
            api.send_event(
                severity="CRITICAL",
                message=f"Proposal {pid} stuck in EXECUTING while MT5 disconnected — fail closed",
                metadata={"proposalId": pid},
            )
            continue

        found = _find_matching_mt5_fill(
            client,
            magic=config.mt5_magic_number,
            comment=comment,
            symbol=str(prop["symbol"]),
        )
        if found is not None:
            api.post_order_result(
                {
                    "proposalId": pid,
                    "brokerAccountId": str(prop["brokerAccountId"]),
                    "actionType": "OPEN",
                    "idempotencyKey": idem,
                    "symbol": str(prop["symbol"]),
                    "side": prop["direction"],
                    "volume": str(found["volume"]),
                    "entryPrice": str(found["entryPrice"]),
                    "brokerOrderId": found.get("brokerOrderId"),
                    "brokerDealId": found.get("brokerDealId"),
                    "brokerPositionId": found.get("brokerPositionId"),
                    "brokerComment": comment,
                    "orderStatus": "FILLED",
                    "proposalStatus": "EXECUTED",
                    "eventPayload": {"reconciledFromMt5": True},
                }
            )
        else:
            api.post_order_result(
                {
                    "proposalId": pid,
                    "brokerAccountId": str(prop["brokerAccountId"]),
                    "actionType": "OPEN",
                    "idempotencyKey": idem,
                    "symbol": str(prop["symbol"]),
                    "side": prop["direction"],
                    "volume": str(prop["volume"]),
                    "brokerComment": comment,
                    "orderStatus": "CANCELLED",
                    "proposalStatus": "EXECUTION_FAILED",
                    "failureReason": "No matching MT5 order/deal found during recovery",
                    "eventPayload": {"reconciledAbsent": True},
                }
            )
        summary["reconciledProposals"] += 1

    for pos in stuck_positions:
        pos_id = str(pos["id"])
        ticket = int(pos["brokerPositionId"])
        idem = f"reconcile-close-{pos_id}"
        close_side: Literal["BUY", "SELL"] = (
            "SELL" if pos["side"] == "BUY" else "BUY"
        )
        if not mt5_ok:
            summary["unknownCount"] += 1
            api.post_order_result(
                {
                    "positionId": pos_id,
                    "brokerAccountId": str(pos["brokerAccountId"]),
                    "actionType": "CLOSE",
                    "idempotencyKey": idem,
                    "symbol": str(pos["symbol"]),
                    "side": close_side,
                    "volume": str(pos["volume"]),
                    "brokerPositionId": str(ticket),
                    "orderStatus": "UNKNOWN",
                    "positionStatus": "UNKNOWN",
                    "eventPayload": {"reconciliation": "disconnected"},
                }
            )
            continue

        still_open = bool(client.positions_get(ticket=ticket))
        if still_open:
            api.post_order_result(
                {
                    "positionId": pos_id,
                    "brokerAccountId": str(pos["brokerAccountId"]),
                    "actionType": "CLOSE",
                    "idempotencyKey": idem,
                    "symbol": str(pos["symbol"]),
                    "side": close_side,
                    "volume": str(pos["volume"]),
                    "brokerPositionId": str(ticket),
                    "orderStatus": "CANCELLED",
                    "positionStatus": "OPEN",
                    "failureReason": "Close timed out; position still open on MT5",
                    "eventPayload": {"revertedToOpen": True},
                }
            )
        else:
            deals = client.history_deals_get(position=ticket) or []
            closing_deal = deals[-1] if deals else None
            api.post_order_result(
                {
                    "positionId": pos_id,
                    "brokerAccountId": str(pos["brokerAccountId"]),
                    "actionType": "CLOSE",
                    "idempotencyKey": idem,
                    "symbol": str(pos["symbol"]),
                    "side": close_side,
                    "volume": str(pos["volume"]),
                    "brokerPositionId": str(ticket),
                    "orderStatus": "FILLED",
                    "positionStatus": "CLOSED",
                    "closePrice": (
                        str(closing_deal.price) if closing_deal else None
                    ),
                    "realizedProfit": (
                        str(getattr(closing_deal, "profit", 0))
                        if closing_deal
                        else "0"
                    ),
                    "commission": (
                        str(getattr(closing_deal, "commission", 0))
                        if closing_deal
                        else "0"
                    ),
                    "swap": (
                        str(getattr(closing_deal, "swap", 0)) if closing_deal else "0"
                    ),
                    "eventPayload": {"reconciledClosed": True},
                }
            )
        summary["reconciledPositions"] += 1

    return summary
