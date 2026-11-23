"""Redis Streams command consumer job dispatching EXECUTE_PROPOSAL & CLOSE_POSITION."""

from __future__ import annotations

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.mt5.client import MT5Client
from app.mt5.execution import (
    ExecutionOutcome,
    execute_approved_proposal,
    execute_manual_close,
)
from app.queue.locks import ExecutionLockManager
from app.queue.streams import (
    ClosePositionCommand,
    ExecuteProposalCommand,
    TradingCommandStream,
)


def process_pending_commands(
    *,
    stream: TradingCommandStream,
    client: MT5Client,
    api: WorkerApiClient,
    locks: ExecutionLockManager,
    config: WorkerConfig,
    block_ms: int = 500,
) -> list[ExecutionOutcome]:
    """Read a batch of commands, execute sequentially, and XACK after persistence."""
    commands = stream.read_commands(count=5, block_ms=block_ms)
    outcomes: list[ExecutionOutcome] = []

    for entry_id, cmd in commands:
        if isinstance(cmd, ExecuteProposalCommand):
            outcome = execute_approved_proposal(
                cmd=cmd,
                client=client,
                api=api,
                locks=locks,
                config=config,
            )
        elif isinstance(cmd, ClosePositionCommand):
            outcome = execute_manual_close(
                cmd=cmd,
                client=client,
                api=api,
                locks=locks,
                config=config,
            )
        else:
            continue

        if outcome.status != "LOCK_BUSY":
            stream.ack(entry_id)
        outcomes.append(outcome)

    return outcomes
