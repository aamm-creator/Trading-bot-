from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ExchangeConfig:
    id: str
    api_key: str
    api_secret: str
    api_passphrase: str
    symbol: str
    timeframe: str
    use_margin: bool
    margin_mode: str
    leverage: float


@dataclass
class TradingConfig:
    dry_run: bool
    paper_equity_eur: float
    poll_seconds: int
    candles_limit: int
    allow_short: bool
    maker_only: bool
    risk_per_trade: float
    max_daily_drawdown: float
    max_notional_eur: float
    fee_rate_maker: float
    fee_rate_taker: float


@dataclass
class StrategyConfig:
    ema_fast: int
    ema_slow: int
    breakout_lookback: int
    atr_period: int
    atr_stop_mult: float
    rr_take_profit: float
    min_volume_ratio: float
    min_atr_percentile: float


@dataclass
class StorageConfig:
    state_file: str
    trades_file: str
    backtest_trades_file: str


@dataclass
class BotConfig:
    exchange: ExchangeConfig
    trading: TradingConfig
    strategy: StrategyConfig
    storage: StorageConfig


def _required(data: dict[str, Any], key: str) -> Any:
    if key not in data:
        raise KeyError(f"Missing required config field: {key}")
    return data[key]


def load_config(path: str | Path) -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    exch = _required(raw, "exchange")
    trading = _required(raw, "trading")
    strategy = _required(raw, "strategy")
    storage = _required(raw, "storage")

    return BotConfig(
        exchange=ExchangeConfig(
            id=str(_required(exch, "id")),
            api_key=str(exch.get("api_key", "")),
            api_secret=str(exch.get("api_secret", "")),
            api_passphrase=str(exch.get("api_passphrase", "")),
            symbol=str(_required(exch, "symbol")),
            timeframe=str(_required(exch, "timeframe")),
            use_margin=bool(exch.get("use_margin", False)),
            margin_mode=str(exch.get("margin_mode", "isolated")),
            leverage=float(exch.get("leverage", 1.0)),
        ),
        trading=TradingConfig(
            dry_run=bool(trading.get("dry_run", True)),
            paper_equity_eur=float(trading.get("paper_equity_eur", 10.0)),
            poll_seconds=int(trading.get("poll_seconds", 60)),
            candles_limit=int(trading.get("candles_limit", 400)),
            allow_short=bool(trading.get("allow_short", False)),
            maker_only=bool(trading.get("maker_only", True)),
            risk_per_trade=float(trading.get("risk_per_trade", 0.005)),
            max_daily_drawdown=float(trading.get("max_daily_drawdown", 0.02)),
            max_notional_eur=float(trading.get("max_notional_eur", 100.0)),
            fee_rate_maker=float(trading.get("fee_rate_maker", 0.0025)),
            fee_rate_taker=float(trading.get("fee_rate_taker", 0.004)),
        ),
        strategy=StrategyConfig(
            ema_fast=int(strategy.get("ema_fast", 20)),
            ema_slow=int(strategy.get("ema_slow", 50)),
            breakout_lookback=int(strategy.get("breakout_lookback", 20)),
            atr_period=int(strategy.get("atr_period", 14)),
            atr_stop_mult=float(strategy.get("atr_stop_mult", 1.8)),
            rr_take_profit=float(strategy.get("rr_take_profit", 1.6)),
            min_volume_ratio=float(strategy.get("min_volume_ratio", 1.2)),
            min_atr_percentile=float(strategy.get("min_atr_percentile", 0.4)),
        ),
        storage=StorageConfig(
            state_file=str(storage.get("state_file", "state.json")),
            trades_file=str(storage.get("trades_file", "trades.csv")),
            backtest_trades_file=str(
                storage.get("backtest_trades_file", "backtest_trades.csv")
            ),
        ),
    )