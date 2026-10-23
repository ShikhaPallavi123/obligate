"""SQLite persistence. One file, no setup. Swap for DynamoDB in production."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .risk import score

SCHEMA = """
CREATE TABLE IF NOT EXISTS obligations (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    owner TEXT,
    kind TEXT NOT NULL,
    due_date TEXT,
    due_phrase TEXT,
    hedged INTEGER DEFAULT 0,
    confidence REAL,
    source_quote TEXT,
    speaker TEXT,
    meeting_title TEXT,
    meeting_date TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    engine TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("OBLIGATE_DB", str(Path.cwd() / "obligate.db"))
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def add(self, *, task: str, owner: str | None, kind: str, due_date: date | None = None,
            due_phrase: str | None = None, hedged: bool = False, confidence: float = 1.0,
            source_quote: str = "", speaker: str | None = None, meeting_title: str | None = None,
            meeting_date: date | None = None, engine: str = "manual") -> str:
        oid = "OB-" + uuid.uuid4().hex[:6].upper()
        self.db.execute(
            "INSERT INTO obligations (id, task, owner, kind, due_date, due_phrase, hedged, confidence,"
            " source_quote, speaker, meeting_title, meeting_date, created_at, engine)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, task, owner, kind, due_date.isoformat() if due_date else None, due_phrase, int(hedged),
             confidence, source_quote, speaker, meeting_title,
             meeting_date.isoformat() if meeting_date else None, _now(), engine))
        self.db.commit()
        return oid

    def find_open_duplicate(self, task: str, owner: str | None) -> str | None:
        row = self.db.execute(
            "SELECT id FROM obligations WHERE status='open' AND lower(task)=lower(?) AND"
            " coalesce(lower(owner),'')=coalesce(lower(?),'')", (task, owner)).fetchone()
        return row["id"] if row else None

    def get(self, oid: str) -> dict | None:
        row = self.db.execute("SELECT * FROM obligations WHERE upper(id)=upper(?)", (oid,)).fetchone()
        return self._enrich(row) if row else None

    def update(self, oid: str, **fields) -> dict | None:
        if not fields:
            return self.get(oid)
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [v.isoformat() if isinstance(v, date) else v for v in fields.values()]
        self.db.execute(f"UPDATE obligations SET {cols} WHERE upper(id)=upper(?)", (*vals, oid))
        self.db.commit()
        return self.get(oid)

    def all(self, status: str | None = "open") -> list[dict]:
        if status:
            rows = self.db.execute("SELECT * FROM obligations WHERE status=? ORDER BY due_date IS NULL, due_date",
                                   (status,)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM obligations ORDER BY due_date IS NULL, due_date").fetchall()
        return [self._enrich(r) for r in rows]

    def clear(self) -> None:
        self.db.execute("DELETE FROM obligations")
        self.db.commit()

    @staticmethod
    def _enrich(row: sqlite3.Row, today: date | None = None) -> dict:
        d = dict(row)
        if today is None:
            override = os.environ.get("OBLIGATE_TODAY")
            today = date.fromisoformat(override) if override else date.today()
        due = date.fromisoformat(d["due_date"]) if d["due_date"] else None
        level, reasons, tags = score(d["task"], d["owner"], due, bool(d["hedged"]), d["kind"], today, d["status"])
        d["hedged"] = bool(d["hedged"])
        d["risk_level"] = level
        d["risk_reasons"] = reasons
        d["compliance_tags"] = tags
        d["days_until_due"] = (due - today).days if due else None
        return d


def to_json(x) -> str:
    return json.dumps(x, indent=2, default=str)
