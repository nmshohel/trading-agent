"""Deterministic technical indicator calculations (EMA, RSI, ATR) on closed candles."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel

from app.mt5.market_data import CandleBar


class IndicatorSnapshot(BaseModel):
    ema_20: float
    ema_50: float
    ema_200: float | None = None
    rsi_14: float
    atr_14: float


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average matching standard trading chart convention."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def compute_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """Wilder-smoothed RSI."""
    delta = closes.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 and avg_gain > 0 -> RSI is 100; both 0 -> RSI is 50
    rsi = rsi.where(avg_loss != 0.0, np.where(avg_gain > 0.0, 100.0, 50.0))
    rsi = rsi.where(closes.index >= period, np.nan)
    return rsi


def compute_atr(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14
) -> pd.Series:
    """Wilder-smoothed Average True Range."""
    prev_close = closes.shift(1)
    tr = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def candles_to_dataframe(bars: list[CandleBar]) -> pd.DataFrame:
    """Convert chronological CandleBar list into a clean float DataFrame."""
    ordered = sorted(bars, key=lambda b: b.candle_time)
    return pd.DataFrame(
        {
            "candle_time": [b.candle_time for b in ordered],
            "open": [float(b.open) for b in ordered],
            "high": [float(b.high) for b in ordered],
            "low": [float(b.low) for b in ordered],
            "close": [float(b.close) for b in ordered],
            "volume": [float(b.volume) for b in ordered],
        }
    )


def compute_indicator_frame(
    bars: list[CandleBar],
    *,
    ema_fast: int = 20,
    ema_mid: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Compute EMA(fast/mid/slow), RSI, and ATR columns on closed candles."""
    df = candles_to_dataframe(bars)
    if df.empty:
        return df

    df["ema_fast"] = compute_ema(df["close"], ema_fast)
    df["ema_mid"] = compute_ema(df["close"], ema_mid)
    df["ema_slow"] = compute_ema(df["close"], ema_slow)
    df["rsi"] = compute_rsi(df["close"], rsi_period)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], atr_period)
    return df


def latest_indicators(
    bars: list[CandleBar],
    *,
    ema_fast: int = 20,
    ema_mid: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> IndicatorSnapshot | None:
    """Return latest closed-candle indicator values, or None if insufficient history."""
    df = compute_indicator_frame(
        bars,
        ema_fast=ema_fast,
        ema_mid=ema_mid,
        ema_slow=ema_slow,
        rsi_period=rsi_period,
        atr_period=atr_period,
    )
    if df.empty:
        return None

    last = df.iloc[-1]
    if (
        pd.isna(last["ema_fast"])
        or pd.isna(last["ema_mid"])
        or pd.isna(last["rsi"])
        or pd.isna(last["atr"])
    ):
        return None

    ema_200_val = None if pd.isna(last["ema_slow"]) else round(float(last["ema_slow"]), 5)
    return IndicatorSnapshot(
        ema_20=round(float(last["ema_fast"]), 5),
        ema_50=round(float(last["ema_mid"]), 5),
        ema_200=ema_200_val,
        rsi_14=round(float(last["rsi"]), 4),
        atr_14=round(float(last["atr"]), 5),
    )
