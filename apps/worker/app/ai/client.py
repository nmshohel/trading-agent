"""Trigger-only OpenAI explanation/veto client with strict timeout & fallback."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from openai import OpenAI
from pydantic import ValidationError

from app.ai.prompts import PROMPT_VERSION, build_review_messages
from app.ai.schemas import AiAnalysisOutcome, AiReviewPayload
from app.risk.validator import RiskValidationResult
from app.strategy.signal import SetupCandidate


def _deterministic_rationale(candidate: SetupCandidate, prefix: str | None = None) -> str:
    parts: list[str] = []
    if prefix:
        parts.append(prefix)
    parts.extend(candidate.deterministic_reasons)
    return " | ".join(parts)


class AiAnalyzer:
    """Reviews validated deterministic candidates without ever controlling prices/lots."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 5.0,
        fallback_mode: Literal["DETERMINISTIC_ONLY", "SKIP"] = "DETERMINISTIC_ONLY",
        openai_client: Any | None = None,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._fallback_mode = fallback_mode
        self._client = openai_client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self._client = OpenAI(
            api_key=self._api_key,
            timeout=self._timeout_seconds,
            max_retries=0,
        )
        return self._client

    def _fallback_outcome(
        self,
        *,
        candidate: SetupCandidate,
        latency_ms: int,
        error_message: str,
        raw_response: dict[str, Any] | None = None,
    ) -> AiAnalysisOutcome:
        if self._fallback_mode == "SKIP":
            return AiAnalysisOutcome(
                ai_status="UNAVAILABLE",
                should_create_proposal=False,
                target_proposal_status=None,
                rationale=_deterministic_rationale(
                    candidate, prefix="AI unavailable — setup skipped per policy"
                ),
                ai_model=self._model,
                ai_prompt_version=PROMPT_VERSION,
                ai_latency_ms=latency_ms,
                ai_raw_response=raw_response,
                error_message=error_message,
            )

        return AiAnalysisOutcome(
            ai_status="UNAVAILABLE",
            should_create_proposal=True,
            target_proposal_status="PENDING_APPROVAL",
            rationale=_deterministic_rationale(
                candidate, prefix="[AI Explanation Unavailable — Deterministic Summary]"
            ),
            ai_model=self._model,
            ai_prompt_version=PROMPT_VERSION,
            ai_latency_ms=latency_ms,
            ai_raw_response=raw_response,
            error_message=error_message,
        )

    def analyze_candidate(
        self,
        *,
        candidate: SetupCandidate,
        risk_result: RiskValidationResult,
        account_currency: str = "USC",
    ) -> AiAnalysisOutcome:
        """Analyze a risk-approved SetupCandidate; never invoked on rejected setups."""
        if not risk_result.approved:
            return AiAnalysisOutcome(
                ai_status="SKIPPED",
                should_create_proposal=False,
                target_proposal_status=None,
                rationale="Skipped AI review because deterministic risk validation rejected setup",
                ai_model=None,
                ai_prompt_version=PROMPT_VERSION,
                ai_latency_ms=0,
            )

        messages = build_review_messages(
            candidate=candidate,
            risk_result=risk_result,
            account_currency=account_currency,
        )
        started = time.perf_counter()

        try:
            client = self._get_client()
            resp = client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.2,
                timeout=self._timeout_seconds,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            return self._fallback_outcome(
                candidate=candidate,
                latency_ms=latency_ms,
                error_message=f"AI request failed: {exc}",
            )

        raw_content = ""
        tokens_used: int | None = None
        try:
            raw_content = str(resp.choices[0].message.content or "")
            usage = getattr(resp, "usage", None)
            if usage is not None and getattr(usage, "total_tokens", None) is not None:
                tokens_used = int(usage.total_tokens)
        except Exception as exc:
            return self._fallback_outcome(
                candidate=candidate,
                latency_ms=latency_ms,
                error_message=f"AI response envelope malformed: {exc}",
            )

        try:
            parsed_raw = json.loads(raw_content)
            if not isinstance(parsed_raw, dict):
                raise ValueError("AI JSON payload must be an object")
            review = AiReviewPayload.model_validate(parsed_raw)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            return self._fallback_outcome(
                candidate=candidate,
                latency_ms=latency_ms,
                error_message=f"AI schema validation failed: {exc}",
                raw_response={"rawContent": raw_content[:2000]},
            )

        rationale = f"[{review.market_regime}] " + " | ".join(review.reasons)
        if review.recommendation == "VETO":
            return AiAnalysisOutcome(
                ai_status="VETOED",
                should_create_proposal=True,
                target_proposal_status="AI_VETOED",
                rationale=rationale,
                review=review,
                ai_model=self._model,
                ai_prompt_version=PROMPT_VERSION,
                ai_latency_ms=latency_ms,
                ai_tokens_used=tokens_used,
                ai_raw_response=parsed_raw,
            )

        return AiAnalysisOutcome(
            ai_status="COMPLETED",
            should_create_proposal=True,
            target_proposal_status="PENDING_APPROVAL",
            rationale=rationale,
            review=review,
            ai_model=self._model,
            ai_prompt_version=PROMPT_VERSION,
            ai_latency_ms=latency_ms,
            ai_tokens_used=tokens_used,
            ai_raw_response=parsed_raw,
        )
