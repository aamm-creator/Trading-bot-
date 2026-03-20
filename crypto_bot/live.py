from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone

from .config import BotConfig, load_config
from .exchange_client import ExchangeClient
from .indicators import with_features
from .journal import append_trade
from .risk import daily_drawdown_hit, size_position
from .state import BotState, Position, load_state, roll_day_if_needed, save_state
from .strategy import evaluate


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fee_rate(cfg: BotConfig) -> float:
    if cfg.trading.maker_only:
        return cfg.trading.fee_rate_maker
    return cfg.trading.fee_rate_taker


def _paper_fill_price(client: ExchangeClient, side: str, reference_price: float) -> float:
    if client.trading.maker_only:
        return client.best_maker_price(side, reference_price)
    return reference_price


def _entry_event(
    state: BotState,
    cfg: BotConfig,
    side: str,
    price: float,
    amount: float,
    notional: float,
    leverage: float,
    reason: str,
    order_id: str,
) -> None:
    fee = notional * _fee_rate(cfg)
    equity_before = state.paper_equity
    if cfg.trading.dry_run:
        state.paper_equity -= fee
    equity_after = state.paper_equity

    append_trade(
        cfg.storage.trades_file,
        {
            "timestamp": _utc_now(),
            "event": "ENTRY",
            "side": side,
            "price": price,
            "amount": amount,
            "notional": notional,
            "leverage": leverage,
            "equity_before": equity_before,
            "equity_after": equity_after,
            "fees": fee,
            "pnl": 0.0,
            "reason": reason,
            "order_id": order_id,
        },
    )


def _exit_event(
    state: BotState,
    cfg: BotConfig,
    position: Position,
    exit_price: float,
    reason: str,
    order_id: str,
) -> float:
    notional = position.amount * exit_price
    gross_pnl = (
        (exit_price - position.entry_price) * position.amount
        if position.side == "long"
        else (position.entry_price - exit_price) * position.amount
    )
    fee = notional * _fee_rate(cfg)
    net_pnl = gross_pnl - fee

    equity_before = state.paper_equity
    if cfg.trading.dry_run:
        state.paper_equity += net_pnl
    equity_after = state.paper_equity

    append_trade(
        cfg.storage.trades_file,
        {
            "timestamp": _utc_now(),
            "event": "EXIT",
            "side": position.side,
            "price": exit_price,
            "amount": position.amount,
            "notional": notional,
            "leverage": position.leverage,
            "equity_before": equity_before,
            "equity_after": equity_after,
            "fees": fee,
            "pnl": net_pnl,
            "reason": reason,
            "order_id": order_id,
        },
    )
    return net_pnl


def run(config_path: str) -> None:
    cfg = load_config(config_path)
    client = ExchangeClient(cfg.exchange, cfg.trading)
    state = load_state(cfg.storage.state_file, cfg.trading.paper_equity_eur)

    if not cfg.trading.dry_run:
        boot_equity = client.fetch_live_quote_equity()
        looks_uninitialized = (
            state.paper_equity == cfg.trading.paper_equity_eur
            and state.day_start_equity == cfg.trading.paper_equity_eur
        )
        if looks_uninitialized:
            state.day_start_equity = boot_equity
        state.paper_equity = boot_equity
        save_state(cfg.storage.state_file, state)

    print("Starting bot with config:")
    print(asdict(cfg))

    while True:
        try:
            df = client.fetch_ohlcv(cfg.trading.candles_limit)
            df = with_features(df, cfg.strategy)
            px = float(df.iloc[-1]["close"])

            live_equity = (
                state.paper_equity
                if cfg.trading.dry_run
                else client.fetch_live_quote_equity()
            )
            if not cfg.trading.dry_run:
                state.paper_equity = live_equity

            roll_day_if_needed(state, live_equity)

            breaker = daily_drawdown_hit(
                state.day_start_equity,
                live_equity,
                cfg.trading.max_daily_drawdown,
            )

            decision = evaluate(df, cfg.strategy, cfg.trading.allow_short)

            if state.position is None:
                if breaker:
                    print("Daily loss limit reached. Skipping new entries.")
                    save_state(cfg.storage.state_file, state)
                    time.sleep(cfg.trading.poll_seconds)
                    continue

                side = None
                direction = None
                if decision.long_entry:
                    side = "buy"
                    direction = "long"
                elif decision.short_entry:
                    side = "sell"
                    direction = "short"

                if side is None:
                    print(f"No entry signal ({decision.reason}).")
                    save_state(cfg.storage.state_file, state)
                    time.sleep(cfg.trading.poll_seconds)
                    continue

                stop_distance = decision.atr_value * cfg.strategy.atr_stop_mult
                sizing = size_position(
                    equity=live_equity,
                    entry_price=px,
                    stop_distance=stop_distance,
                    risk_per_trade=cfg.trading.risk_per_trade,
                    leverage=cfg.exchange.leverage,
                    max_notional=cfg.trading.max_notional_eur,
                )

                if sizing.amount <= 0:
                    print("Size is zero. Skipping.")
                    save_state(cfg.storage.state_file, state)
                    time.sleep(cfg.trading.poll_seconds)
                    continue

                if cfg.trading.dry_run:
                    fill_price = _paper_fill_price(client, side, px)
                    filled_amount = sizing.amount
                    order_id = f"paper-entry-{int(time.time())}"
                else:
                    order = client.place_order(
                        side=side,
                        amount=sizing.amount,
                        reference_price=px,
                        leverage=cfg.exchange.leverage,
                        maker_only=cfg.trading.maker_only,
                        reduce_only=False,
                    )
                    fill_price = order.price
                    filled_amount = order.amount
                    order_id = order.order_id

                if filled_amount <= 0:
                    print("Entry order returned zero fill. Skipping.")
                    save_state(cfg.storage.state_file, state)
                    time.sleep(cfg.trading.poll_seconds)
                    continue

                notional = filled_amount * fill_price
                leverage_used = (notional / live_equity) if live_equity > 0 else 0.0

                if direction == "long":
                    stop_price = fill_price - stop_distance
                    take_profit = fill_price + stop_distance * cfg.strategy.rr_take_profit
                else:
                    stop_price = fill_price + stop_distance
                    take_profit = fill_price - stop_distance * cfg.strategy.rr_take_profit

                state.position = Position(
                    side=direction,
                    amount=filled_amount,
                    entry_price=fill_price,
                    stop_price=stop_price,
                    take_profit=take_profit,
                    notional=notional,
                    leverage=leverage_used,
                    opened_at=_utc_now(),
                )

                _entry_event(
                    state=state,
                    cfg=cfg,
                    side=direction,
                    price=fill_price,
                    amount=filled_amount,
                    notional=notional,
                    leverage=leverage_used,
                    reason=decision.reason,
                    order_id=order_id,
                )
                print(
                    f"Entered {direction}: price={fill_price:.4f}, amount={filled_amount:.8f}, "
                    f"stop={stop_price:.4f}, tp={take_profit:.4f}"
                )

            else:
                pos = state.position
                assert pos is not None

                # Trail stop with ATR so we lock gains if trend persists.
                trail = decision.atr_value * cfg.strategy.atr_stop_mult
                if pos.side == "long":
                    pos.stop_price = max(pos.stop_price, px - trail)
                else:
                    pos.stop_price = min(pos.stop_price, px + trail)

                exit_reason = ""
                if pos.side == "long":
                    if px <= pos.stop_price:
                        exit_reason = "stop_loss"
                    elif px >= pos.take_profit:
                        exit_reason = "take_profit"
                    elif decision.long_exit:
                        exit_reason = "trend_reversal"
                else:
                    if px >= pos.stop_price:
                        exit_reason = "stop_loss"
                    elif px <= pos.take_profit:
                        exit_reason = "take_profit"
                    elif decision.short_exit:
                        exit_reason = "trend_reversal"

                if exit_reason:
                    close_side = "sell" if pos.side == "long" else "buy"
                    if cfg.trading.dry_run:
                        exit_price = _paper_fill_price(client, close_side, px)
                        filled_amount = pos.amount
                        order_id = f"paper-exit-{int(time.time())}"
                    else:
                        # Exits are market-by-default for safer risk-off behavior.
                        order = client.place_order(
                            side=close_side,
                            amount=pos.amount,
                            reference_price=px,
                            leverage=cfg.exchange.leverage,
                            maker_only=False,
                            reduce_only=True,
                        )
                        exit_price = order.price
                        filled_amount = order.amount
                        order_id = order.order_id

                    if filled_amount <= 0:
                        print("Exit order returned zero fill. Retrying next loop.")
                        save_state(cfg.storage.state_file, state)
                        time.sleep(cfg.trading.poll_seconds)
                        continue

                    if filled_amount < pos.amount:
                        # Keep position state coherent in case of partial close.
                        remaining_ratio = (pos.amount - filled_amount) / pos.amount
                        closed_piece = Position(
                            side=pos.side,
                            amount=filled_amount,
                            entry_price=pos.entry_price,
                            stop_price=pos.stop_price,
                            take_profit=pos.take_profit,
                            notional=pos.notional * (1.0 - remaining_ratio),
                            leverage=pos.leverage,
                            opened_at=pos.opened_at,
                        )
                        pnl = _exit_event(
                            state=state,
                            cfg=cfg,
                            position=closed_piece,
                            exit_price=exit_price,
                            reason=f"{exit_reason}_partial",
                            order_id=order_id,
                        )
                        pos.amount = pos.amount - filled_amount
                        pos.notional = pos.amount * pos.entry_price
                        print(
                            f"Partially exited {filled_amount:.8f}; remaining={pos.amount:.8f}, pnl={pnl:.4f}"
                        )
                    else:
                        pnl = _exit_event(
                            state=state,
                            cfg=cfg,
                            position=pos,
                            exit_price=exit_price,
                            reason=exit_reason,
                            order_id=order_id,
                        )
                        state.position = None
                        print(
                            f"Exited {pos.side} at {exit_price:.4f}, reason={exit_reason}, pnl={pnl:.4f}"
                        )
                else:
                    print(
                        f"Holding {pos.side}. last={px:.4f} stop={pos.stop_price:.4f} tp={pos.take_profit:.4f}"
                    )

            save_state(cfg.storage.state_file, state)
            time.sleep(cfg.trading.poll_seconds)

        except KeyboardInterrupt:
            print("Stopping bot.")
            save_state(cfg.storage.state_file, state)
            return
        except Exception as exc:
            print(f"Loop error: {exc}")
            save_state(cfg.storage.state_file, state)
            time.sleep(cfg.trading.poll_seconds)