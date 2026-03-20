from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionSizing:
    amount: float
    notional: float
    leverage_used: float


def size_position(
    equity: float,
    entry_price: float,
    stop_distance: float,
    risk_per_trade: float,
    leverage: float,
    max_notional: float,
) -> PositionSizing:
    if equity <= 0 or entry_price <= 0 or stop_distance <= 0:
        return PositionSizing(0.0, 0.0, 0.0)

    capped_leverage = max(1.0, min(leverage, 10.0))
    risk_budget = equity * risk_per_trade

    # Position loss at stop = amount * stop_distance.
    amount_by_risk = risk_budget / stop_distance
    notional_by_risk = amount_by_risk * entry_price

    leverage_cap_notional = equity * capped_leverage
    effective_notional = min(notional_by_risk, leverage_cap_notional, max_notional)

    if effective_notional <= 0:
        return PositionSizing(0.0, 0.0, 0.0)

    amount = effective_notional / entry_price
    leverage_used = effective_notional / equity
    return PositionSizing(amount, effective_notional, leverage_used)


def daily_drawdown_hit(
    day_start_equity: float,
    current_equity: float,
    max_daily_drawdown: float,
) -> bool:
    if day_start_equity <= 0:
        return False

    drawdown = (current_equity - day_start_equity) / day_start_equity
    return drawdown <= -abs(max_daily_drawdown)