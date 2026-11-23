"""Redis Streams consumer group for EXECUTE_PROPOSAL & CLOSE_POSITION commands."""

from __future__ import annotations

import json
from typing import Any, Literal

import redis as redis_lib
from pydantic import BaseModel, ValidationError


class ExecuteProposalPayload(BaseModel):
    proposalId: str
    approvalId: str
    accountLogin: str
    symbol: str
    autoApproved: bool = False


class ExecuteProposalCommand(BaseModel):
    type: Literal["EXECUTE_PROPOSAL"]
    commandId: str
    idempotencyKey: str
    createdAt: str
    payload: ExecuteProposalPayload


class ClosePositionPayload(BaseModel):
    positionId: str
    brokerPositionId: str
    accountLogin: str
    symbol: str
    requestedByUserId: str


class ClosePositionCommand(BaseModel):
    type: Literal["CLOSE_POSITION"]
    commandId: str
    idempotencyKey: str
    createdAt: str
    payload: ClosePositionPayload


TradingCommand = ExecuteProposalCommand | ClosePositionCommand


def parse_trading_command(raw_json: str) -> TradingCommand:
    data = json.loads(raw_json)
    cmd_type = data.get("type") if isinstance(data, dict) else None
    if cmd_type == "EXECUTE_PROPOSAL":
        return ExecuteProposalCommand.model_validate(data)
    if cmd_type == "CLOSE_POSITION":
        return ClosePositionCommand.model_validate(data)
    raise ValueError(f"Unsupported command type: {cmd_type}")


class TradingCommandStream:
    """Consumes commands via XREADGROUP with XACK after reconciliation & DLQ routing."""

    def __init__(
        self,
        redis_url: str,
        *,
        stream_name: str = "commands:trading",
        dlq_stream_name: str = "commands:trading:dlq",
        group_name: str = "trading-workers",
        consumer_name: str = "win-mt5-1",
        redis_client: Any | None = None,
    ) -> None:
        self._stream = stream_name
        self._dlq = dlq_stream_name
        self._group = group_name
        self._consumer = consumer_name
        self._owns_client = redis_client is None
        self._redis = (
            redis_client
            if redis_client is not None
            else redis_lib.Redis.from_url(redis_url, decode_responses=True)
        )

    def close(self) -> None:
        if self._owns_client:
            self._redis.close()

    def ensure_group(self) -> None:
        try:
            self._redis.xgroup_create(
                self._stream, self._group, id="0", mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def read_commands(
        self, *, count: int = 5, block_ms: int = 1000
    ) -> list[tuple[str, TradingCommand]]:
        raw_streams = self._redis.xreadgroup(
            groupname=self._group,
            consumername=self._consumer,
            streams={self._stream: ">"},
            count=count,
            block=block_ms,
        )
        if not raw_streams:
            return []

        commands: list[tuple[str, TradingCommand]] = []
        for _stream_key, entries in raw_streams:
            for entry_id, fields in entries:
                raw_payload = fields.get("payload", "")
                try:
                    cmd = parse_trading_command(raw_payload)
                    commands.append((str(entry_id), cmd))
                except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                    self.send_to_dlq(str(entry_id), raw_payload, str(exc))
        return commands

    def ack(self, entry_id: str) -> None:
        self._redis.xack(self._stream, self._group, entry_id)

    def send_to_dlq(self, entry_id: str, raw_payload: str, error: str) -> None:
        self._redis.xadd(
            self._dlq,
            {
                "originalEntryId": entry_id,
                "error": error[:500],
                "payload": raw_payload[:4000],
            },
        )
        self.ack(entry_id)
