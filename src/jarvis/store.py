"""SQLite durable operational state. Short-lived connections are thread-safe."""
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value):
    if isinstance(value, dict):
        return {str(k): v if k in {"prompt_tokens", "completion_tokens", "total_tokens"} and type(v) is int
                else "[redacted]" if any(term in str(k).lower() for term in (
            "secret", "token", "password", "api_key", "authorization", "content", "prompt", "message", "transcript", "text"
        )) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value[:100]]
    if isinstance(value, str):
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9_.-]+", "Bearer [redacted]", value[:1000])
    return value


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("database cannot be a symlink")
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS audit (
                  id INTEGER PRIMARY KEY, time TEXT NOT NULL, kind TEXT NOT NULL, details TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, tool TEXT NOT NULL,
                  fingerprint TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                  error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS focus (
                  id TEXT PRIMARY KEY, task TEXT NOT NULL, minutes INTEGER NOT NULL,
                  status TEXT NOT NULL, started TEXT NOT NULL, updated TEXT NOT NULL,
                  completed TEXT, elapsed REAL NOT NULL DEFAULT 0);
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def audit(self, kind: str, details: dict):
        with self.connect() as db:
            db.execute("INSERT INTO audit(time,kind,details) VALUES(?,?,?)",
                       (now(), kind[:80], json.dumps(redact(details), ensure_ascii=False)))

    def events(self, limit=100):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))
            return [{**dict(row), "details": json.loads(row["details"])} for row in rows]

    def metrics(self):
        with self.connect() as db:
            counts = {row["status"]: row["n"] for row in
                      db.execute("SELECT status,count(*) n FROM jobs GROUP BY status")}
            return {"jobs": counts, "audit_events": db.execute("SELECT count(*) FROM audit").fetchone()[0]}
