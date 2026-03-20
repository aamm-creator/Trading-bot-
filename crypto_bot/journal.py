from __future__ import annotations

import csv
from pathlib import Path
from typing import Mapping


TRADE_COLUMNS = [
    "timestamp",
    "event",
    "side",
    "price",
    "amount",
    "notional",
    "leverage",
    "equity_before",
    "equity_after",
    "fees",
    "pnl",
    "reason",
    "order_id",
]


def append_trade(path: str, row: Mapping[str, object]) -> None:
    file_path = Path(path)
    write_header = not file_path.exists()

    with file_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_COLUMNS)
        if write_header:
            writer.writeheader()

        payload = {k: row.get(k, "") for k in TRADE_COLUMNS}
        writer.writerow(payload)