"""Closed-candle market scan job: strategy -> deterministic risk -> AI -> API proposal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.ai.client import AiAnalyzer
from app.api_client import WorkerApiClient
from app.market.features import BlackoutWindowSpec
from app.mt5.account import VerifiedAccount
from app.mt5.client import MT5Client
from app.mt5.market_data import CandleBar, LiveQuote
from app.mt5.symbols import VerifiedSymbolSpecs
from app.risk.limits import RiskSnapshotState
from app.risk.validator import PreTradeRiskContext, validate_pre_trade
from app.strategy.base import BaseStrategy
from app.strategy.signal import StrategyContext


def run_market_scan(
    *,
    client: MT5Client,
    api: WorkerApiClient,
    strategy: BaseStrategy,
    ai_analyzer: AiAnalyzer,
    account: VerifiedAccount,
    broker_account_id: str,
    symbol_specs: VerifiedSymbolSpecs,
    quote: LiveQuote,
    h1_bars: list[CandleBar],
    m15_bars: list[CandleBar],
    now_utc: datetime,
    kill_switch_active: bool = False,
    is_paused: bool = False,
    risk_percent: Decimal = Decimal("0.25"),
    min_reward_risk: Decimal = Decimal("2.00"),
    max_spread: Decimal = Decimal("0.50"),
    commission_per_lot: Decimal = Decimal("0.00"),
    approval_expiry_seconds: int = 120,
    open_positions_count: int = 0,
    daily_trades_count: int = 0,
    max_daily_trades: int = 5,
    pending_proposals_count: int = 0,
    blackouts: list[BlackoutWindowSpec] | None = None,
    cooldown_until: datetime | None = None,
    existing_dedup_keys: set[str] | None = None,
    daily_risk: RiskSnapshotState | None = None,
    weekly_risk: RiskSnapshotState | None = None,
) -> dict[str, Any] | None:
    """Evaluate closed candles and submit a validated proposal if all gates pass."""
    if kill_switch_active or is_paused:
        return None

    strat_ctx = StrategyContext(
        symbol=symbol_specs.symbol,
        h1_bars=h1_bars,
        m15_bars=m15_bars,
        current_bid=quote.bid,
        current_ask=quote.ask,
        max_spread=max_spread,
        now_utc=now_utc,
        blackouts=blackouts or [],
        cooldown_until=cooldown_until,
        existing_dedup_keys=existing_dedup_keys or set(),
    )
    candidate = strategy.evaluate(strat_ctx)
    if candidate is None:
        return None

    risk_ctx = PreTradeRiskContext(
        account=account,
        symbol_specs=symbol_specs,
        quote=quote,
        direction=candidate.direction,
        entry_reference=candidate.entry_reference,
        stop_loss=candidate.stop_loss,
        take_profit=candidate.take_profit,
        now_utc=now_utc,
        kill_switch_active=kill_switch_active,
        is_paused=is_paused,
        risk_percent=risk_percent,
        min_reward_risk=min_reward_risk,
        max_spread=max_spread,
        commission_per_lot=commission_per_lot,
        open_positions_count=open_positions_count,
        daily_trades_count=daily_trades_count,
        max_daily_trades=max_daily_trades,
        pending_proposals_count=pending_proposals_count,
        blackouts=blackouts or [],
        daily_risk=daily_risk,
        weekly_risk=weekly_risk,
    )
    risk_result = validate_pre_trade(client, risk_ctx)
    if not risk_result.approved:
        api.send_event(
            severity="INFO",
            message=f"Candidate rejected by risk engine: {', '.join(risk_result.rejection_codes)}",
            metadata={
                "dedupKey": candidate.dedup_key,
                "reasons": risk_result.rejection_reasons,
            },
        )
        return None

    ai_outcome = ai_analyzer.analyze_candidate(
        candidate=candidate,
        risk_result=risk_result,
        account_currency=account.account_currency,
    )
    if not ai_outcome.should_create_proposal or ai_outcome.target_proposal_status is None:
        api.send_event(
            severity="WARN",
            message=f"Proposal skipped due to AI fallback policy: {ai_outcome.error_message}",
            metadata={"dedupKey": candidate.dedup_key},
        )
        return None

    expires_at = now_utc.astimezone(UTC) + timedelta(seconds=approval_expiry_seconds)
    payload: dict[str, Any] = {
        "dedupKey": candidate.dedup_key,
        "brokerAccountId": broker_account_id,
        "strategyId": candidate.strategy_id,
        "symbol": candidate.symbol,
        "timeframe": candidate.timeframe,
        "direction": candidate.direction,
        "entry": str(candidate.entry_reference),
        "stopLoss": str(candidate.stop_loss),
        "takeProfit": str(candidate.take_profit),
        "volume": str(risk_result.volume),
        "riskAmount": str(risk_result.estimated_total_loss),
        "riskPercent": str(risk_result.estimated_loss_percent),
        "rewardRisk": str(risk_result.reward_risk),
        "estimatedMargin": (
            str(risk_result.estimated_margin)
            if risk_result.estimated_margin is not None
            else None
        ),
        "spreadAtProposal": str(quote.spread),
        "rationale": ai_outcome.rationale,
        "aiStatus": ai_outcome.ai_status,
        "aiModel": ai_outcome.ai_model,
        "aiPromptVersion": ai_outcome.ai_prompt_version,
        "aiLatencyMs": ai_outcome.ai_latency_ms,
        "aiTokensUsed": ai_outcome.ai_tokens_used,
        "aiRawResponse": ai_outcome.ai_raw_response,
        "status": ai_outcome.target_proposal_status,
        "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
    }
    return api.create_proposal(payload)
