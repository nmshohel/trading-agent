"""Trading worker entrypoint — Full live loop (MT5 sync, SSE, M15 scanner, Streams execution)."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.ai.client import AiAnalyzer
from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.jobs.command_consumer import process_pending_commands
from app.jobs.market_scan import run_market_scan
from app.jobs.position_sync import run_position_sync
from app.jobs.proposal_expiry import run_proposal_expiry
from app.market.candles import CandleSyncTracker, QuoteThrottler
from app.market.features import parse_blackouts
from app.monitoring.health import build_heartbeat_payload, sync_and_heartbeat
from app.monitoring.logging import get_logger
from app.mt5.account import AccountVerificationError, verify_account
from app.mt5.client import MT5Client, MT5ConnectionError
from app.mt5.execution import _build_risk_snapshot, reconcile_stuck_states
from app.mt5.market_data import (
    SupportedTimeframe,
    detect_live_server_offset,
    fetch_closed_candles,
    fetch_live_tick,
)
from app.mt5.symbols import SymbolVerificationError, verify_symbol
from app.queue.locks import ExecutionLockManager
from app.queue.pubsub import SseEventPublisher
from app.queue.streams import TradingCommandStream
from app.strategy.base import BaseStrategy
from app.strategy.xauusd_ew_v1 import XauusdElliottWaveV1Strategy
from app.strategy.xauusd_v1 import XauusdV1Strategy

logger = get_logger("trading_worker.main")
SYNC_TIMEFRAMES: tuple[SupportedTimeframe, ...] = ("M5", "M15", "H1")


def _sanitized_openai_key(raw_key: str) -> str:
    key = raw_key.strip()
    if not key or key.startswith("replace_with_"):
        return ""
    return key


def _parse_optional_iso(raw: Any) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def sync_market_data(
    *,
    client: MT5Client,
    api: WorkerApiClient,
    pubsub: SseEventPublisher | None,
    symbol: str,
    offset_seconds: int,
    throttler: QuoteThrottler,
    tracker: CandleSyncTracker,
    initial_candle_count: int = 10,
) -> datetime | None:
    """Sync live quote & newly closed candles; returns newest closed M15 candle_time."""
    quote = fetch_live_tick(client, symbol, offset_seconds)
    if quote is None:
        return None

    if pubsub is not None and throttler.should_publish(quote):
        try:
            pubsub.publish_quote(
                symbol=quote.symbol,
                bid=str(quote.bid),
                ask=str(quote.ask),
                spread=str(quote.spread),
                quote_time=quote.quote_time,
            )
        except Exception as exc:
            logger.warning(
                "Redis Pub/Sub quote publish skipped (Redis offline?)",
                extra={"extra_data": {"error": str(exc)}},
            )

    latest_m5_time: datetime | None = None
    latest_m15_time: datetime | None = None
    for tf in SYNC_TIMEFRAMES:
        bars = fetch_closed_candles(
            client, symbol, tf, initial_candle_count, offset_seconds
        )
        if tf == "M5" and bars:
            latest_m5_time = bars[-1].candle_time
        if tf == "M15" and bars:
            latest_m15_time = bars[-1].candle_time
        for bar in tracker.filter_unsynced(bars):
            api.push_market_snapshot(
                bar.to_snapshot_payload(
                    bid=quote.bid, ask=quote.ask, spread=quote.spread
                )
            )
            tracker.mark_synced(bar)

    return latest_m5_time or latest_m15_time


def _build_strategy_from_row(
    row: dict[str, Any], price_digits: int
) -> BaseStrategy | None:
    name = str(row.get("name", ""))
    params = row.get("parameters") or {}
    if name == "xauusd_ew_v1":
        return XauusdElliottWaveV1Strategy(
            strategy_id=str(row["id"]),
            version=str(row.get("version", "2.0.0")),
            ema_fast=int(params.get("emaFast", 21)),
            ema_slow=int(params.get("emaSlow", 144)),
            rsi_period=int(params.get("rsiPeriod", 14)),
            atr_period=int(params.get("atrPeriod", 14)),
            zigzag_atr_mult=float(params.get("zigzagAtrMult", 1.5)),
            sl_atr_buffer_multiplier=float(params.get("slAtrBufferMultiplier", 0.75)),
            target_reward_risk=float(params.get("targetRewardRisk", 2.0)),
            min_smc_confluence=int(params.get("minSmcConfluence", 2)),
            min_anchor_divergences=int(params.get("minAnchorDivergences", 2)),
            enable_wave_3=bool(params.get("enableWave3", True)),
            enable_wave_5=bool(params.get("enableWave5", True)),
            enable_abc=bool(params.get("enableAbc", True)),
            price_digits=price_digits,
        )
    if name == "xauusd_v1":
        return XauusdV1Strategy(
            strategy_id=str(row["id"]),
            version=str(row.get("version", "1.0.0")),
            ema_fast=int(params.get("emaFast", 20)),
            ema_mid=int(params.get("emaMid", 50)),
            ema_slow=int(params.get("emaSlow", 200)),
            rsi_period=int(params.get("rsiPeriod", 14)),
            atr_period=int(params.get("atrPeriod", 14)),
            sl_atr_buffer_multiplier=float(params.get("slAtrBufferMultiplier", 0.5)),
            target_reward_risk=float(params.get("targetRewardRisk", 2.0)),
            price_digits=price_digits,
        )
    return None


def run_m15_strategy_scan(
    *,
    client: MT5Client,
    api: WorkerApiClient,
    config: WorkerConfig,
    offset_seconds: int,
    scanned_dedup_keys: set[str],
) -> bool:
    """Evaluate active M15 strategies (xauusd_ew_v1, xauusd_v1) on closed candles."""
    runtime = api.get_runtime_state()
    if runtime.get("killSwitchActive") or runtime.get("isPaused"):
        return False

    strategies = runtime.get("strategies") or []
    active_rows = [
        s
        for s in strategies
        if s.get("isActive") and s.get("name") in ("xauusd_ew_v1", "xauusd_v1")
    ]
    if not active_rows:
        return False

    # Prefer Elliott Wave first when multiple strategies are active
    active_rows.sort(key=lambda s: 0 if s.get("name") == "xauusd_ew_v1" else 1)

    account_row = runtime.get("account") or {}
    broker_account_id = account_row.get("id")
    if not broker_account_id:
        return False

    account = verify_account(client, config, require_trade_allowed=False)
    symbol_specs = verify_symbol(client, config.mt5_symbol)
    quote = fetch_live_tick(client, symbol_specs.symbol, offset_seconds)
    if quote is None:
        return False

    h1_bars = fetch_closed_candles(client, symbol_specs.symbol, "H1", 220, offset_seconds)
    m15_bars = fetch_closed_candles(
        client, symbol_specs.symbol, "M15", 140, offset_seconds
    )
    m5_bars = fetch_closed_candles(
        client, symbol_specs.symbol, "M5", 180, offset_seconds
    )
    if not h1_bars or not m15_bars:
        return False

    settings = runtime.get("settings") or {}
    ai_fallback = str(settings.get("aiFallbackMode", config.ai_fallback_mode))
    analyzer = AiAnalyzer(
        api_key=_sanitized_openai_key(config.openai_api_key),
        model=config.openai_model,
        timeout_seconds=config.ai_timeout_seconds,
        fallback_mode=(
            "SKIP" if ai_fallback == "SKIP" else "DETERMINISTIC_ONLY"
        ),
    )

    open_positions = client.positions_get(symbol=symbol_specs.symbol) or []
    for row in active_rows:
        strategy = _build_strategy_from_row(row, symbol_specs.digits)
        if strategy is None:
            continue
        try:
            created = run_market_scan(
                client=client,
                api=api,
                strategy=strategy,
                ai_analyzer=analyzer,
                account=account,
                broker_account_id=str(broker_account_id),
                symbol_specs=symbol_specs,
                quote=quote,
                h1_bars=h1_bars,
                m15_bars=m15_bars,
                m5_bars=m5_bars,
                now_utc=datetime.now(UTC),
                kill_switch_active=bool(runtime.get("killSwitchActive", False)),
                is_paused=bool(runtime.get("isPaused", False)),
                risk_percent=Decimal(str(settings.get("riskPercent", "0.25"))),
                min_reward_risk=Decimal(str(settings.get("minRewardRisk", "2.00"))),
                max_spread=Decimal(str(settings.get("maxSpread", "0.50"))),
                commission_per_lot=Decimal(str(settings.get("commissionPerLot", "0.00"))),
                approval_expiry_seconds=int(settings.get("approvalExpirySeconds", 120)),
                open_positions_count=len(open_positions),
                daily_trades_count=int(runtime.get("dailyTradesCount", 0)),
                max_daily_trades=int(settings.get("maxDailyTrades", 5)),
                blackouts=parse_blackouts(runtime.get("blackouts") or []),
                cooldown_until=_parse_optional_iso(settings.get("cooldownUntil")),
                existing_dedup_keys=scanned_dedup_keys,
                daily_risk=_build_risk_snapshot(runtime.get("dailySnapshot")),
                weekly_risk=_build_risk_snapshot(runtime.get("weeklySnapshot")),
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                logger.info(
                    "Proposal skipped by API conflict (duplicate dedupKey or max pending)",
                    extra={"extra_data": {"detail": exc.response.text}},
                )
                continue
            raise

        if created and isinstance(created.get("proposal"), dict):
            dk = created["proposal"].get("dedupKey")
            if dk:
                scanned_dedup_keys.add(str(dk))
            logger.info(
                "Auto MTF (H1/M15/M5) scanner created proposal",
                extra={
                    "extra_data": {
                        "strategy": strategy.name,
                        "proposalId": created["proposal"].get("id"),
                        "status": created["proposal"].get("status"),
                        "timeframe": created["proposal"].get("timeframe"),
                        "m5CandleTime": (
                            m5_bars[-1].candle_time.isoformat() if m5_bars else None
                        ),
                        "m15CandleTime": m15_bars[-1].candle_time.isoformat(),
                    }
                },
            )
            return True

    logger.info(
        "MTF (H1/M15/M5) closed-candle scan completed (no actionable setup)",
        extra={
            "extra_data": {
                "symbol": symbol_specs.symbol,
                "strategies": [r.get("name") for r in active_rows],
                "m5CandleTime": m5_bars[-1].candle_time.isoformat() if m5_bars else None,
                "m15CandleTime": m15_bars[-1].candle_time.isoformat(),
            }
        },
    )
    return False


def run_once(
    config: WorkerConfig,
    client: MT5Client,
    api: WorkerApiClient,
    pubsub: SseEventPublisher | None = None,
    throttler: QuoteThrottler | None = None,
    tracker: CandleSyncTracker | None = None,
) -> tuple[str | None, int, datetime | None]:
    """Connect to MT5, verify account & symbol, sync market data, and send heartbeat."""
    client.ensure_connected()
    account = verify_account(client, config, require_trade_allowed=False)
    symbol_specs = verify_symbol(client, config.mt5_symbol)
    offset_seconds = detect_live_server_offset(client, symbol_specs.symbol)

    sync_and_heartbeat(
        api=api,
        config=config,
        account=account,
        symbol_specs=symbol_specs,
        server_time_offset_seconds=offset_seconds,
    )
    logger.info(
        "Connected to Exness MT5 & synced heartbeat to Cloud API",
        extra={
            "extra_data": {
                "login": account.login,
                "mode": account.mode,
                "balance": str(account.balance),
                "symbol": symbol_specs.symbol,
            }
        },
    )

    latest_m15 = sync_market_data(
        client=client,
        api=api,
        pubsub=pubsub,
        symbol=symbol_specs.symbol,
        offset_seconds=offset_seconds,
        throttler=throttler or QuoteThrottler(),
        tracker=tracker or CandleSyncTracker(),
    )

    # Refresh heartbeat after candle sync so cloud status never expires on cold start
    api.send_heartbeat(
        build_heartbeat_payload(
            config=config,
            mt5_connected=True,
            account=account,
            symbol_specs=symbol_specs,
            server_time_offset_seconds=offset_seconds,
        )
    )

    runtime = api.get_runtime_state()
    broker_account_id = (runtime.get("account") or {}).get("id")
    if broker_account_id:
        run_position_sync(
            client=client,
            api=api,
            config=config,
            broker_account_id=str(broker_account_id),
            server_offset_seconds=offset_seconds,
        )

    logger.info(
        "MT5 verification, account sync, and market snapshot sync succeeded",
        extra={
            "extra_data": {
                "login": account.login,
                "mode": account.mode,
                "accountType": account.account_type,
                "symbol": symbol_specs.symbol,
                "fillingMode": symbol_specs.filling_mode,
                "serverTimeOffsetSeconds": offset_seconds,
            }
        },
    )
    return (
        str(broker_account_id) if broker_account_id else None,
        offset_seconds,
        latest_m15,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exness MT5 Trading Worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one verification/sync cycle and exit",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Main poll interval in seconds (default: 1.0)",
    )
    args = parser.parse_args(argv)

    config = WorkerConfig()
    client = MT5Client(terminal_path=config.mt5_terminal_path)
    api = WorkerApiClient(
        base_url=config.worker_api_base_url,
        auth_token=config.worker_auth_token,
    )
    pubsub = SseEventPublisher(
        redis_url=config.redis_url,
        channel=config.redis_pubsub_channel,
    )
    locks = ExecutionLockManager(redis_url=config.redis_url)
    stream = TradingCommandStream(
        redis_url=config.redis_url,
        stream_name=config.redis_stream_commands,
    )
    throttler = QuoteThrottler(min_interval_seconds=1.0)
    tracker = CandleSyncTracker()

    stopping = False

    def _handle_signal(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        logger.info(
            "Starting Exness MT5 Trading Worker",
            extra={
                "extra_data": {
                    "apiBaseUrl": config.worker_api_base_url,
                    "symbol": config.mt5_symbol,
                    "mode": config.mt5_account_mode,
                }
            },
        )
        if args.once:
            run_once(config, client, api, pubsub, throttler, tracker)
            return 0

        try:
            stream.ensure_group()
        except Exception as exc:
            logger.warning(
                "Redis consumer group init failed at startup",
                extra={"extra_data": {"error": str(exc)}},
            )

        try:
            reconcile_stuck_states(client=client, api=api, config=config)
        except Exception as exc:
            logger.warning(
                "Startup stuck-state reconciliation warning",
                extra={"extra_data": {"error": str(exc)}},
            )

        last_full_sync = 0.0
        last_pos_sync = 0.0
        last_reconcile = time.monotonic()
        last_scanned_m15: datetime | None = None
        scanned_dedup_keys: set[str] = set()
        broker_account_id: str | None = None
        offset_seconds = 0

        while not stopping:
            now = time.monotonic()
            try:
                latest_m15: datetime | None = None
                if now - last_full_sync >= 10.0:
                    broker_account_id, offset_seconds, latest_m15 = run_once(
                        config, client, api, pubsub, throttler, tracker
                    )
                    run_proposal_expiry(api)
                    last_full_sync = time.monotonic()
                    last_pos_sync = last_full_sync
                else:
                    latest_m15 = sync_market_data(
                        client=client,
                        api=api,
                        pubsub=pubsub,
                        symbol=config.mt5_symbol,
                        offset_seconds=offset_seconds,
                        throttler=throttler,
                        tracker=tracker,
                        initial_candle_count=5,
                    )
                    if broker_account_id and (now - last_pos_sync >= 3.0):
                        run_position_sync(
                            client=client,
                            api=api,
                            config=config,
                            broker_account_id=broker_account_id,
                            server_offset_seconds=offset_seconds,
                        )
                        last_pos_sync = now

                # Run automatic strategy scan once per newly closed M15 candle
                if (
                    broker_account_id
                    and latest_m15 is not None
                    and (last_scanned_m15 is None or latest_m15 > last_scanned_m15)
                ):
                    run_m15_strategy_scan(
                        client=client,
                        api=api,
                        config=config,
                        offset_seconds=offset_seconds,
                        scanned_dedup_keys=scanned_dedup_keys,
                    )
                    last_scanned_m15 = latest_m15

                # Process EXECUTE_PROPOSAL & CLOSE_POSITION commands from Redis Streams
                try:
                    outcomes = process_pending_commands(
                        stream=stream,
                        client=client,
                        api=api,
                        locks=locks,
                        config=config,
                        block_ms=250,
                    )
                    for outcome in outcomes:
                        logger.info(
                            "Processed trading command",
                            extra={"extra_data": outcome.model_dump()},
                        )
                except Exception as exc:
                    logger.warning(
                        "Command consumer poll error",
                        extra={"extra_data": {"error": str(exc)}},
                    )

                if now - last_reconcile >= 45.0:
                    reconcile_stuck_states(client=client, api=api, config=config)
                    last_reconcile = now

            except (
                MT5ConnectionError,
                AccountVerificationError,
                SymbolVerificationError,
            ) as exc:
                logger.error(
                    "MT5 verification failed (fail-closed)",
                    extra={"extra_data": {"error": str(exc)}},
                )
                try:
                    api.send_heartbeat(
                        build_heartbeat_payload(
                            worker_id="win-mt5-1",
                            account=None,
                            symbol=config.mt5_symbol,
                            server_time_offset_seconds=None,
                            mt5_connected=False,
                        )
                    )
                    api.send_event(
                        severity="ERROR",
                        message=f"MT5 verification failed: {exc}",
                        metadata={"symbol": config.mt5_symbol},
                    )
                except Exception:
                    logger.exception("Failed to report MT5 error to internal API")
            except Exception:
                logger.exception("Unexpected error in worker loop")

            time.sleep(max(0.25, args.poll_interval))
        return 0
    finally:
        client.shutdown()
        stream.close()
        locks.close()
        pubsub.close()
        api.close()


if __name__ == "__main__":
    sys.exit(main())
