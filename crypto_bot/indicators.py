from __future__ import annotations

import numpy as np
import pandas as pd

from .config import StrategyConfig


# ---------------------------------------------------------------------------
# Core building blocks
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx(df: pd.DataFrame, period: int) -> pd.Series:
    """Compute ADX (Average Directional Index) using Wilder smoothing."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=df.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=df.index,
    )

    tr_h_l = high - low
    tr_h_c = (high - close.shift(1)).abs()
    tr_l_c = (low - close.shift(1)).abs()
    tr = pd.concat([tr_h_l, tr_h_c, tr_l_c], axis=1).max(axis=1)

    alpha = 1.0 / period
    atr_smooth = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_smooth
    minus_di = 100 * minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr_smooth

    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1) * 100
    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    return adx


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """Compute RSI using Wilder smoothing."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    alpha = 1.0 / period
    avg_gain = gain.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# Sentiment proxy (Fear & Greed approximation from price data)
# ---------------------------------------------------------------------------

def _sentiment_proxy(df: pd.DataFrame, lookback: int) -> pd.Series:
    """
    Create a 0-100 sentiment score from price data alone.
    Combines:
      - Price momentum (where is price vs recent range)    40%
      - Volatility (high vol = fear, low vol = greed)      30%
      - Drawdown from recent high                          30%

    Low score = fear (good time to buy aggressively)
    High score = greed (reduce risk / take profits)
    """
    close = df["close"]

    # Momentum: where is price in its recent range (0=bottom, 100=top)
    rolling_high = close.rolling(lookback).max()
    rolling_low = close.rolling(lookback).min()
    price_range = rolling_high - rolling_low
    momentum = ((close - rolling_low) / price_range.replace(0, 1)) * 100

    # Volatility: high recent vol = fear. Rank recent vol vs history.
    returns = close.pct_change()
    recent_vol = returns.rolling(lookback).std()
    vol_rank = recent_vol.rolling(lookback * 2).rank(pct=True).fillna(0.5)
    # Invert: high vol = low score (fear)
    vol_score = (1 - vol_rank) * 100

    # Drawdown from recent high: large drawdown = fear
    dd = (close - rolling_high) / rolling_high.replace(0, 1)
    # dd is negative; map -0.5 → 0, 0.0 → 100
    dd_score = ((dd + 0.5) / 0.5).clip(0, 1) * 100

    sentiment = momentum * 0.4 + vol_score * 0.3 + dd_score * 0.3
    return sentiment


# ---------------------------------------------------------------------------
# Main feature builder
# ---------------------------------------------------------------------------

def with_features(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    out = df.copy()

    # --- EMAs: fast, slow, regime (200) ---
    out["ema_fast"] = out["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    out["ema_slow"] = out["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    out["ema_regime"] = out["close"].ewm(span=cfg.ema_regime, adjust=False).mean()

    # EMA slope
    out["ema_fast_slope"] = out["ema_fast"] - out["ema_fast"].shift(cfg.ema_slope_lookback)

    # --- ATR ---
    out["atr"] = _atr(out, cfg.atr_period)
    out["atr_pct"] = out["atr"] / out["close"]

    # --- ADX ---
    out["adx"] = _adx(out, cfg.adx_period)

    # --- RSI (Layer 3: mean reversion) ---
    out["rsi"] = _rsi(out["close"], cfg.rsi_period)

    # --- Bollinger Bands (Layer 2) ---
    bb_mid = out["close"].rolling(cfg.bb_period).mean()
    bb_std = out["close"].rolling(cfg.bb_period).std()
    out["bb_upper"] = bb_mid + cfg.bb_std * bb_std
    out["bb_lower"] = bb_mid - cfg.bb_std * bb_std
    out["bb_mid"] = bb_mid
    out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / bb_mid  # bandwidth %

    # --- Keltner Channels (Layer 2) ---
    kc_mid = out["close"].ewm(span=cfg.kc_period, adjust=False).mean()
    kc_atr = _atr(out, cfg.kc_period)
    out["kc_upper"] = kc_mid + cfg.kc_atr_mult * kc_atr
    out["kc_lower"] = kc_mid - cfg.kc_atr_mult * kc_atr

    # --- Squeeze detection ---
    # Squeeze = BB inside KC (low volatility compression)
    out["squeeze"] = (out["bb_lower"] > out["kc_lower"]) & (out["bb_upper"] < out["kc_upper"])
    # Count consecutive squeeze bars
    squeeze_groups = (~out["squeeze"]).cumsum()
    out["squeeze_count"] = out.groupby(squeeze_groups).cumcount() * out["squeeze"].astype(int)
    # Squeeze just fired = was in squeeze, now released
    out["squeeze_fired"] = out["squeeze"].shift(1).fillna(False) & ~out["squeeze"]

    # --- Breakout channels (kept) ---
    out["breakout_high"] = out["high"].rolling(cfg.breakout_lookback).max().shift(1)
    out["breakout_low"] = out["low"].rolling(cfg.breakout_lookback).min().shift(1)

    # --- Volume filter ---
    out["vol_sma"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / out["vol_sma"]

    # --- Sentiment proxy (Layer 1) ---
    out["sentiment"] = _sentiment_proxy(out, cfg.sentiment_lookback)

    return out
