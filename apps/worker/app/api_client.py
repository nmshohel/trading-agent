"""Authenticated HTTP client for Node.js API /api/internal/v1/* endpoints."""

from __future__ import annotations

from typing import Any

import httpx


class WorkerApiClient:
    """Single gateway for all Python worker reads/writes to the Node.js API."""

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        *,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def ping(self) -> dict[str, Any]:
        res = self._client.get("/api/internal/v1/ping", headers=self._headers)
        res.raise_for_status()
        return res.json()

    def get_runtime_state(self) -> dict[str, Any]:
        res = self._client.get(
            "/api/internal/v1/runtime-state", headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def send_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/heartbeat", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def sync_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/account/sync", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def push_market_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/market/snapshot", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def sync_risk_snapshots(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/risk/snapshots", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def create_proposal(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/proposals", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def expire_proposals(self) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/proposals/expire", headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def claim_proposal(self, proposal_id: str) -> dict[str, Any]:
        res = self._client.post(
            f"/api/internal/v1/proposals/{proposal_id}/claim",
            headers=self._headers,
        )
        res.raise_for_status()
        return res.json()

    def get_stuck_reconciliation(
        self, threshold_seconds: int = 45
    ) -> dict[str, Any]:
        res = self._client.get(
            "/api/internal/v1/reconciliation/stuck",
            params={"thresholdSeconds": threshold_seconds},
            headers=self._headers,
        )
        res.raise_for_status()
        return res.json()

    def post_order_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/orders/result", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def sync_positions(self, payload: dict[str, Any]) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/positions/sync", json=payload, headers=self._headers
        )
        res.raise_for_status()
        return res.json()

    def send_event(
        self,
        *,
        severity: str,
        message: str,
        metadata: dict[str, Any] | None = None,
        service: str = "trading-worker",
    ) -> dict[str, Any]:
        res = self._client.post(
            "/api/internal/v1/events",
            json={
                "service": service,
                "severity": severity,
                "message": message,
                "metadata": metadata or {},
            },
            headers=self._headers,
        )
        res.raise_for_status()
        return res.json()
