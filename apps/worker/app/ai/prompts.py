"""Versioned prompt templates for structured XAUUSD setup explanation & advisory veto."""

from __future__ import annotations

import json
from typing import Any

from app.risk.validator import RiskValidationResult
from app.strategy.signal import SetupCandidate

PROMPT_VERSION = "v1.0"

SYSTEM_PROMPT = """You are a conservative trading-setup reviewer for a personal XAUUSD
semi-automatic trading assistant.

STRICT RULES:
1. A deterministic rule-based strategy and risk engine have ALREADY calculated
   the exact direction, entry reference, Stop Loss, Take Profit, and lot size.
2. You MUST NOT invent indicator values, change prices, change SL/TP, or recommend lot sizes.
3. Evaluate only the provided structured context and return ONLY valid JSON
   with these exact keys (no extra keys):
   {
     "recommendation": "PROCEED" or "VETO",
     "confidence": number between 0.0 and 1.0,
     "market_regime": short regime label (e.g. "BULLISH_CONTINUATION", "CHOPPY_RANGE"),
     "reasons": [1 to 6 concise factual reasons based on the provided data],
     "risks": [1 to 6 concise risk warnings],
     "invalidation_notes": [1 to 6 concise invalidation conditions]
   }
4. Recommend "VETO" if the context shows clear exhaustion, conflicting structure,
   or unfavorable risk-to-noise conditions.
"""


def build_review_messages(
    *,
    candidate: SetupCandidate,
    risk_result: RiskValidationResult,
    account_currency: str,
) -> list[dict[str, str]]:
    """Build sanitized prompt messages with zero credentials or secrets."""
    user_payload: dict[str, Any] = {
        "symbol": candidate.symbol,
        "timeframe": candidate.timeframe,
        "strategy": f"{candidate.strategy_name}@{candidate.strategy_version}",
        "direction": candidate.direction,
        "candleTimeUtc": candidate.candle_time.isoformat(),
        "entryReference": str(candidate.entry_reference),
        "stopLoss": str(candidate.stop_loss),
        "takeProfit": str(candidate.take_profit),
        "rewardRisk": str(candidate.reward_risk),
        "riskSummary": {
            "accountCurrency": account_currency,
            "calculatedVolume": str(risk_result.volume),
            "estimatedMaxLoss": str(risk_result.estimated_total_loss),
            "estimatedLossPercent": str(risk_result.estimated_loss_percent),
        },
        "indicators": candidate.indicators,
        "deterministicReasons": candidate.deterministic_reasons,
        "invalidationRules": candidate.invalidation_rules,
    }

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Review this deterministic XAUUSD candidate setup and respond strictly in JSON:\n"
                + json.dumps(user_payload, indent=2)
            ),
        },
    ]
