"""Periodic proposal expiry job calling the internal API."""

from __future__ import annotations

from app.api_client import WorkerApiClient


def run_proposal_expiry(api: WorkerApiClient) -> int:
    """Trigger server-side expiry of overdue PENDING_APPROVAL proposals."""
    result = api.expire_proposals()
    return int(result.get("expiredCount", 0))
