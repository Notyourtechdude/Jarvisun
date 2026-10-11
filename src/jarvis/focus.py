"""Opt-in focus sessions; progress counts completed outcomes, not nudges."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store, now


class FocusService:
    def __init__(self, store: Store, timezone="Asia/Dubai"):
        self.store, self.timezone = store, ZoneInfo(timezone)

    def status(self):
        today = datetime.now(self.timezone).date()
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM focus ORDER BY started DESC LIMIT 1").fetchone()
            dates = db.execute("SELECT completed FROM focus WHERE completed IS NOT NULL")
            moved = sum(datetime.fromisoformat(r[0]).astimezone(self.timezone).date() == today for r in dates)
        session = dict(row) if row else None
        if session:
            elapsed = session["elapsed"]
            if session["status"] == "active":
                elapsed += (datetime.fromisoformat(now()) - datetime.fromisoformat(session["updated"])).total_seconds()
            session["remaining_seconds"] = max(0, round(session["minutes"] * 60 - elapsed))
            session["next_action"] = "Open the thing you need. Work on one small step for two minutes."
        return {"session": session, "moved_today": moved}

    def action(self, action: str, task: str = "", minutes: int = 15):
        if action not in {"start", "pause", "resume", "complete"}:
            raise ValueError("unknown focus action")
        timestamp = now()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM focus ORDER BY started DESC LIMIT 1").fetchone()
            if action == "start":
                if not task.strip() or len(task) > 500 or not 1 <= minutes <= 180:
                    raise ValueError("task required (<=500 chars); session is 1..180 minutes")
                if row and row["status"] in {"active", "paused"}:
                    raise ValueError("complete the existing session before starting another")
                db.execute("INSERT INTO focus(id,task,minutes,status,started,updated) VALUES(?,?,?,?,?,?)",
                           (str(uuid.uuid4()), task.strip(), minutes, "active", timestamp, timestamp))
            else:
                if not row or row["status"] == "completed":
                    raise ValueError("no unfinished focus session")
                transitions = {"pause": "active", "resume": "paused", "complete": row["status"]}
                if row["status"] != transitions[action]:
                    raise ValueError("invalid focus transition")
                elapsed = row["elapsed"]
                if row["status"] == "active":
                    elapsed += (datetime.fromisoformat(timestamp) - datetime.fromisoformat(row["updated"])).total_seconds()
                state = {"pause": "paused", "resume": "active", "complete": "completed"}[action]
                db.execute("UPDATE focus SET status=?,updated=?,completed=?,elapsed=? WHERE id=?",
                           (state, timestamp, timestamp if action == "complete" else None, elapsed, row["id"]))
        self.store.audit("focus", {"action": action})
        return self.status()
