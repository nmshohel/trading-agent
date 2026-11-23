"""Abstract base interface for deterministic trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.strategy.signal import SetupCandidate, StrategyContext


class BaseStrategy(ABC):
    strategy_id: str
    name: str
    version: str

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> SetupCandidate | None:
        """Evaluate closed-candle context and return a SetupCandidate or None."""
