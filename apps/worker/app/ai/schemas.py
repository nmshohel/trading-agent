"""Strict Pydantic schemas for AI commentary/veto outputs (zero price/lot authority)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AiReviewPayload(BaseModel):
    """Schema enforced on every LLM response.

    `extra="forbid"` guarantees the AI cannot inject `entry_price`, `stop_loss`,
    `take_profit`, `volume`, or any broker command fields.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: Literal["PROCEED", "VETO"]
    confidence: float = Field(ge=0.0, le=1.0)
    market_regime: str = Field(min_length=2, max_length=80)
    reasons: list[str] = Field(min_length=1, max_length=6)
    risks: list[str] = Field(min_length=1, max_length=6)
    invalidation_notes: list[str] = Field(min_length=1, max_length=6)


class AiAnalysisOutcome(BaseModel):
    ai_status: Literal["COMPLETED", "VETOED", "UNAVAILABLE", "SKIPPED"]
    should_create_proposal: bool
    target_proposal_status: Literal["PENDING_APPROVAL", "AI_VETOED"] | None = None
    rationale: str
    review: AiReviewPayload | None = None
    ai_model: str | None = None
    ai_prompt_version: str
    ai_latency_ms: int = 0
    ai_tokens_used: int | None = None
    ai_raw_response: dict[str, Any] | None = None
    error_message: str | None = None
