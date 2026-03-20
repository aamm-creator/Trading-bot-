from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Position:
    side: str
    amount: float
    entry_price: float
    stop_price: float
    take_profit: float
    notional: float
    leverage: float
    opened_at: str


@dataclass
class BotState:
    day: str
    day_start_equity: float
    paper_equity: float
    position: Position | None


def utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def default_state(paper_equity: float) -> BotState:
    day = utc_day()
    return BotState(
        day=day,
        day_start_equity=paper_equity,
        paper_equity=paper_equity,
        position=None,
    )


def load_state(path: str, paper_equity: float) -> BotState:
    p = Path(path)
    if not p.exists():
        return default_state(paper_equity)

    raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    pos_raw = raw.get("position")
    position = Position(**pos_raw) if pos_raw else None
    return BotState(
        day=str(raw.get("day", utc_day())),
        day_start_equity=float(raw.get("day_start_equity", paper_equity)),
        paper_equity=float(raw.get("paper_equity", paper_equity)),
        position=position,
    )


def save_state(path: str, state: BotState) -> None:
    payload = asdict(state)
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def roll_day_if_needed(state: BotState, reference_equity: float) -> None:
    today = utc_day()
    if state.day != today:
        state.day = today
        state.day_start_equity = reference_equity