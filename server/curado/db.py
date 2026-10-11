"""SQLite storage. One connection guarded by a lock: the load is a reading
every 30 s, so there is no need for anything fancier."""

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts INTEGER NOT NULL,
    t REAL, rh REAL,
    s0_t REAL, s0_rh REAL,
    s1_t REAL, s1_rh REAL,
    s2_t REAL, s2_rh REAL,
    compressor INTEGER, humidifier INTEGER, fan INTEGER,
    cooling INTEGER, dehumidifying INTEGER,
    alarms TEXT, rssi INTEGER, uptime INTEGER
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings (ts);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_ts ON photos (ts);

CREATE TABLE IF NOT EXISTS hams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_ts INTEGER NOT NULL,
    initial_weight_g REAL NOT NULL,
    target_loss_pct REAL NOT NULL DEFAULT 33,
    notes TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS weights (
    id INTEGER PRIMARY KEY,
    ham_id INTEGER NOT NULL REFERENCES hams (id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    weight_g REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def get_json(self, key: str, default=None):
        row = self.query_one("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(row["value"]) if row else default

    def set_json(self, key: str, value) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
