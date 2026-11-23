"""Phase 5 AI analysis tests: schema enforcement, veto, fallback & trigger-only guard."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.ai.client import AiAnalyzer
from app.ai.prompts import PROMPT_VERSION
from app.risk.validator import RiskValidationResult
from app.strategy.signal import SetupCandidate


class FakeOpenAIClient:
    def __init__(
        self,
        *,
        content: str | None = None,
        total_tokens: int = 310,
        raise_exc: Exception | None = None,
    ) -> None:
        self.call_count = 0
        self._content = content
        self._total_tokens = total_tokens
        self._raise_exc = raise_exc
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs: Any) -> Any:
        self.call_count += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self._content))
            ],
            usage=SimpleNamespace(total_tokens=self._total_tokens),
        )


def _candidate() -> SetupCandidate:
    return SetupCandidate(
        strategy_id="xauusd_v1",
        strategy_name="xauusd_v1",
        strategy_version="1.0.0",
        symbol="XAUUSDc",
        timeframe="M15",
        direction="BUY",
        candle_time=datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC),
        entry_reference=Decimal("2650.20"),
        stop_loss=Decimal("2645.20"),
        take_profit=Decimal("2660.20"),
        reward_risk=Decimal("2.00"),
        dedup_key="xauusd_v1:XAUUSDc:M15:2026-11-03T13:00:00Z:BUY",
        deterministic_reasons=["H1 bullish EMA stack", "M15 higher-low pullback"],
        invalidation_rules=["Close below 2645.20 invalidates setup"],
        indicators={"m15": {"rsi_14": 58.2, "atr_14": 3.4}},
    )


def _approved_risk() -> RiskValidationResult:
    return RiskValidationResult(
        approved=True,
        volume=Decimal("0.09"),
        risk_budget=Decimal("50.0000"),
        estimated_total_loss=Decimal("47.8800"),
        estimated_loss_percent=Decimal("0.4788"),
        estimated_margin=Decimal("13.5000"),
        reward_risk=Decimal("2.00"),
    )


def test_ai_proceed_returns_completed_with_audit_metadata() -> None:
    payload = {
        "recommendation": "PROCEED",
        "confidence": 0.78,
        "market_regime": "BULLISH_CONTINUATION",
        "reasons": ["H1 trend cleanly aligned", "M15 pullback respected EMA20"],
        "risks": ["Approaching intraday high"],
        "invalidation_notes": ["Break below 2645.20 invalidates bullish thesis"],
    }
    fake = FakeOpenAIClient(content=json.dumps(payload), total_tokens=412)
    analyzer = AiAnalyzer(api_key="test", model="gpt-4o-mini", openai_client=fake)

    outcome = analyzer.analyze_candidate(
        candidate=_candidate(), risk_result=_approved_risk()
    )
    assert fake.call_count == 1
    assert outcome.ai_status == "COMPLETED"
    assert outcome.should_create_proposal is True
    assert outcome.target_proposal_status == "PENDING_APPROVAL"
    assert outcome.ai_prompt_version == PROMPT_VERSION
    assert outcome.ai_tokens_used == 412
    assert outcome.review is not None
    assert outcome.review.confidence == 0.78


def test_ai_veto_marks_proposal_ai_vetoed() -> None:
    payload = {
        "recommendation": "VETO",
        "confidence": 0.85,
        "market_regime": "EXHAUSTION_SPIKE",
        "reasons": ["Extended move into major resistance"],
        "risks": ["High whipsaw probability"],
        "invalidation_notes": ["Wait for deeper retest"],
    }
    fake = FakeOpenAIClient(content=json.dumps(payload))
    analyzer = AiAnalyzer(api_key="test", openai_client=fake)

    outcome = analyzer.analyze_candidate(
        candidate=_candidate(), risk_result=_approved_risk()
    )
    assert outcome.ai_status == "VETOED"
    assert outcome.should_create_proposal is True
    assert outcome.target_proposal_status == "AI_VETOED"


def test_ai_cannot_inject_price_or_lot_overrides_and_falls_back() -> None:
    bad_payload = {
        "recommendation": "PROCEED",
        "confidence": 0.80,
        "market_regime": "BULLISH",
        "reasons": ["Looks good"],
        "risks": ["Normal volatility"],
        "invalidation_notes": ["Below swing low"],
        "stop_loss": 2640.00,  # FORBIDDEN extra field!
        "volume": 1.00,  # FORBIDDEN extra field!
    }
    fake = FakeOpenAIClient(content=json.dumps(bad_payload))
    analyzer = AiAnalyzer(
        api_key="test",
        fallback_mode="DETERMINISTIC_ONLY",
        openai_client=fake,
    )

    outcome = analyzer.analyze_candidate(
        candidate=_candidate(), risk_result=_approved_risk()
    )
    assert outcome.ai_status == "UNAVAILABLE"
    assert outcome.should_create_proposal is True
    assert outcome.target_proposal_status == "PENDING_APPROVAL"
    assert outcome.error_message is not None
    assert "schema validation failed" in outcome.error_message


def test_ai_timeout_obeys_deterministic_only_and_skip_fallback_modes() -> None:
    fake_timeout = FakeOpenAIClient(raise_exc=TimeoutError("OpenAI request timed out"))

    det = AiAnalyzer(
        api_key="test", fallback_mode="DETERMINISTIC_ONLY", openai_client=fake_timeout
    )
    out_det = det.analyze_candidate(
        candidate=_candidate(), risk_result=_approved_risk()
    )
    assert out_det.ai_status == "UNAVAILABLE"
    assert out_det.should_create_proposal is True
    assert out_det.target_proposal_status == "PENDING_APPROVAL"
    assert "AI Explanation Unavailable" in out_det.rationale

    skip = AiAnalyzer(
        api_key="test", fallback_mode="SKIP", openai_client=fake_timeout
    )
    out_skip = skip.analyze_candidate(
        candidate=_candidate(), risk_result=_approved_risk()
    )
    assert out_skip.ai_status == "UNAVAILABLE"
    assert out_skip.should_create_proposal is False
    assert out_skip.target_proposal_status is None


def test_ai_is_never_called_when_risk_validation_rejects_setup() -> None:
    fake = FakeOpenAIClient(content="{}")
    analyzer = AiAnalyzer(api_key="test", openai_client=fake)
    rejected = RiskValidationResult(
        approved=False,
        rejection_codes=["MINIMUM_LOT_EXCEEDS_RISK_BUDGET"],
        rejection_reasons=["Too small equity"],
    )

    outcome = analyzer.analyze_candidate(candidate=_candidate(), risk_result=rejected)
    assert fake.call_count == 0
    assert outcome.ai_status == "SKIPPED"
    assert outcome.should_create_proposal is False
