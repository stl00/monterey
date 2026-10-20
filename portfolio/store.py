"""SQLite snapshot history: one row per holding per sync."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    taken_at TEXT NOT NULL,
    base_ccy TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    source TEXT, account TEXT, symbol TEXT, name TEXT, asset_class TEXT,
    quantity REAL, currency TEXT, market_value REAL, price REAL, cost_basis REAL,
    counted INTEGER, value_base REAL
);
CREATE TABLE IF NOT EXISTS source_status (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    source TEXT, ok INTEGER, message TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def save(self, snapshot) -> int:
        cur = self.db.execute("INSERT INTO snapshots (taken_at, base_ccy) VALUES (?, ?)",
                              (snapshot.taken_at, snapshot.base_ccy))
        sid = cur.lastrowid
        self.db.executemany(
            "INSERT INTO holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(sid, h.source, h.account, h.symbol, h.name, h.asset_class, h.quantity, h.currency,
              h.market_value, h.price, h.cost_basis, int(h.counted), v) for h, v in snapshot.rows],
        )
        self.db.executemany("INSERT INTO source_status VALUES (?,?,?,?)",
                            [(sid, s, int(ok), msg) for s, (ok, msg) in snapshot.status.items()])
        self.db.commit()
        return sid

    def history(self) -> list[sqlite3.Row]:
        """Counted total per snapshot, oldest first."""
        return self.db.execute(
            "SELECT s.taken_at, s.base_ccy, SUM(h.value_base) AS total FROM snapshots s "
            "JOIN holdings h ON h.snapshot_id = s.id AND h.counted = 1 GROUP BY s.id ORDER BY s.id"
        ).fetchall()
