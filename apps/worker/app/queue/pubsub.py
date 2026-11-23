"""Redis Pub/Sub publisher for real-time SSE envelopes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import redis as redis_lib
from pydantic import BaseModel

SseEventType = Literal[
    "quote.updated",
    "proposal.created",
    "proposal.updated",
    "proposal.expired",
    "order.updated",
    "position.updated",
    "position.closed",
    "system.health",
    "risk.alert",
    "settings.updated",
]


class SseEnvelope(BaseModel):
    id: str
    type: SseEventType
    timestamp: str
    data: dict[str, Any]


class SseEventPublisher:
    """Publishes validated SSE JSON envelopes onto Redis Pub/Sub."""

    def __init__(
        self,
        redis_url: str,
        channel: str = "events:sse",
        *,
        redis_client: Any | None = None,
    ) -> None:
        self._channel = channel
        self._owns_client = redis_client is None
        self._redis = (
            redis_client
            if redis_client is not None
            else redis_lib.Redis.from_url(redis_url, decode_responses=True)
        )

    def close(self) -> None:
        if self._owns_client:
            self._redis.close()

    def publish(self, event_type: SseEventType, data: dict[str, Any]) -> SseEnvelope:
        envelope = SseEnvelope(
            id=str(uuid.uuid4()),
            type=event_type,
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            data=data,
        )
        self._redis.publish(self._channel, json.dumps(envelope.model_dump()))
        return envelope

    def publish_quote(
        self,
        *,
        symbol: str,
        bid: str,
        ask: str,
        spread: str,
        quote_time: datetime,
    ) -> SseEnvelope:
        payload = {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "quoteTime": quote_time.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
        if hasattr(self._redis, "set"):
            try:
                self._redis.set("market:latest_quote", json.dumps(payload), ex=120)
            except Exception:
                pass
        return self.publish("quote.updated", payload)

    def publish_risk_alert(
        self, *, code: str, message: str, severity: str = "WARN"
    ) -> SseEnvelope:
        return self.publish(
            "risk.alert",
            {"code": code, "message": message, "severity": severity},
        )
