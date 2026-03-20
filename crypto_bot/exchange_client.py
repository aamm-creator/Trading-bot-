from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .config import ExchangeConfig, TradingConfig

try:
    import ccxt  # type: ignore
except ImportError:  # pragma: no cover
    ccxt = None


@dataclass
class ExecutedOrder:
    order_id: str
    side: str
    amount: float
    price: float
    fee_cost: float
    fee_currency: str
    timestamp: str


class ExchangeClient:
    def __init__(self, cfg: ExchangeConfig, trading: TradingConfig):
        if ccxt is None:
            raise RuntimeError(
                "ccxt is not installed. Run: pip install -r requirements.txt"
            )

        self.cfg = cfg
        self.trading = trading

        exchange_class = getattr(ccxt, cfg.id)
        self.exchange = exchange_class(
            {
                "apiKey": cfg.api_key,
                "secret": cfg.api_secret,
                "password": cfg.api_passphrase or None,
                "enableRateLimit": True,
            }
        )
        self.exchange.load_markets()

    @property
    def quote_currency(self) -> str:
        market = self.exchange.market(self.cfg.symbol)
        return str(market["quote"])

    def fetch_ohlcv(self, limit: int) -> pd.DataFrame:
        rows = self.exchange.fetch_ohlcv(
            self.cfg.symbol, timeframe=self.cfg.timeframe, limit=limit
        )
        if not rows:
            raise RuntimeError("No OHLCV data returned from exchange")

        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    def fetch_last_price(self) -> float:
        ticker = self.exchange.fetch_ticker(self.cfg.symbol)
        value = ticker.get("last") or ticker.get("close")
        if value is None:
            raise RuntimeError("Ticker did not contain last price")
        return float(value)

    def best_maker_price(self, side: str, fallback: float) -> float:
        try:
            orderbook = self.exchange.fetch_order_book(self.cfg.symbol, limit=5)
            best_bid = (
                float(orderbook["bids"][0][0]) if orderbook.get("bids") else fallback
            )
            best_ask = (
                float(orderbook["asks"][0][0]) if orderbook.get("asks") else fallback
            )
        except Exception:
            return fallback

        if side == "buy":
            return best_bid
        return best_ask

    def fetch_live_quote_equity(self) -> float:
        balance = self.exchange.fetch_balance()
        quote = self.quote_currency

        total = balance.get("total", {}).get(quote)
        free = balance.get("free", {}).get(quote)

        if total is not None:
            return float(total)
        if free is not None:
            return float(free)

        raise RuntimeError(f"Could not find {quote} balance in account")

    def _extract_fee(self, order: dict[str, Any]) -> tuple[float, str]:
        fee = order.get("fee") or {}
        if fee:
            return float(fee.get("cost", 0.0) or 0.0), str(
                fee.get("currency", self.quote_currency)
            )

        fees = order.get("fees") or []
        if fees:
            total = 0.0
            currency = self.quote_currency
            for item in fees:
                total += float(item.get("cost", 0.0) or 0.0)
                currency = str(item.get("currency", currency))
            return total, currency

        return 0.0, self.quote_currency

    def place_order(
        self,
        side: str,
        amount: float,
        reference_price: float,
        leverage: float,
        maker_only: bool,
        reduce_only: bool = False,
    ) -> ExecutedOrder:
        amount_prec = float(self.exchange.amount_to_precision(self.cfg.symbol, amount))

        params: dict[str, Any] = {}
        if self.cfg.use_margin:
            params["leverage"] = max(1.0, min(10.0, float(leverage)))
            params["marginMode"] = self.cfg.margin_mode

        if reduce_only:
            params["reduceOnly"] = True

        if self.cfg.id.lower() == "kraken":
            params["trading_agreement"] = "agree"

        order_type = "market"
        price_arg = None
        if maker_only:
            order_type = "limit"
            params["postOnly"] = True
            best_price = self.best_maker_price(side, reference_price)
            price_arg = float(self.exchange.price_to_precision(self.cfg.symbol, best_price))

        order = self.exchange.create_order(
            self.cfg.symbol,
            order_type,
            side,
            amount_prec,
            price_arg,
            params,
        )

        status = str(order.get("status", "")).lower()
        filled = float(order.get("filled") or 0.0)
        if maker_only and filled <= 0 and status in {"open", "new"}:
            order_id = str(order.get("id", ""))
            if order_id:
                try:
                    self.exchange.cancel_order(order_id, self.cfg.symbol)
                except Exception:
                    pass
            raise RuntimeError(
                "Maker order did not fill immediately and was cancelled to avoid desync"
            )

        if 0 < filled < amount_prec:
            order_id = str(order.get("id", ""))
            if order_id:
                try:
                    self.exchange.cancel_order(order_id, self.cfg.symbol)
                except Exception:
                    pass

        effective_amount = filled if filled > 0 else amount_prec

        avg = order.get("average")
        px = order.get("price")
        executed_price = float(avg or px or price_arg or reference_price)

        fee_cost, fee_currency = self._extract_fee(order)

        return ExecutedOrder(
            order_id=str(order.get("id", "unknown")),
            side=side,
            amount=effective_amount,
            price=executed_price,
            fee_cost=fee_cost,
            fee_currency=fee_currency,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )