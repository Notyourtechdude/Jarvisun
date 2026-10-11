"""Recurring standing-policy jobs. Missed executions are not replayed in a burst."""
import json
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo


class WorkflowService:
    def __init__(self, store, jobs, tools):
        self.store, self.jobs, self.tools = store, jobs, tools
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS workflows (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
              interval INTEGER NOT NULL, next_run REAL NOT NULL, paused INTEGER NOT NULL DEFAULT 0)""")

    def create(self, name, tool, arguments, interval=3600):
        self.tools.broker.authorize(tool)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ValueError("workflow name must be 1..100 characters")
        if type(interval) is not int or not 60 <= interval <= 2_592_000:
            raise ValueError("workflow interval must be 60 seconds..30 days")
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 20_000:
            raise ValueError("workflow arguments must be a bounded object")
        self.tools.validate(tool, arguments)
        row = {"id": str(uuid.uuid4()), "name": name.strip(), "tool": tool, "arguments": arguments,
               "interval": interval, "next_run": time.time(), "paused": False}
        with self.store.connect() as db:
            if db.execute("SELECT count(*) FROM workflows").fetchone()[0] >= 100:
                raise ValueError("workflow limit reached")
            db.execute("INSERT INTO workflows VALUES(?,?,?,?,?,?,?)",
                       (row["id"], row["name"], tool, json.dumps(arguments), interval, row["next_run"], 0))
        self.store.audit("workflow_created", {"id": row["id"], "tool": tool, "interval": interval})
        return row

    def list(self):
        with self.store.connect() as db:
            return [{**dict(row), "arguments": json.loads(row["arguments"]), "paused": bool(row["paused"])}
                    for row in db.execute("SELECT * FROM workflows ORDER BY name")]

    def pause(self, workflow_id, paused):
        if not isinstance(paused, bool):
            raise ValueError("paused must be a boolean")
        with self.store.connect() as db:
            if not db.execute("UPDATE workflows SET paused=? WHERE id=?", (int(paused), workflow_id)).rowcount:
                raise ValueError("workflow not found")
        self.store.audit("workflow_pause", {"id": workflow_id, "paused": paused})
        return {"id": workflow_id, "paused": paused}

    def tick(self):
        timestamp = time.time()
        deferred = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM workflows WHERE paused=0 AND next_run<=? LIMIT 10",
                              (timestamp,)).fetchall()
            ready = []
            for row in rows:
                if row["tool"] == "notify" and self.notification_deferred(json.loads(row["arguments"])):
                    # Queue one upcoming notification; never accumulate a nighttime backlog.
                    db.execute("UPDATE workflows SET next_run=? WHERE id=?", (timestamp + 60, row["id"]))
                    deferred.append({"workflow_id": row["id"], "deferred": True,
                                     "reason": "Non-urgent notification queued during quiet hours or focus"})
                    continue
                db.execute("UPDATE workflows SET next_run=? WHERE id=?", (timestamp + row["interval"], row["id"]))
                ready.append(row)
        results = deferred
        for row in ready:
            arguments = json.loads(row["arguments"])
            occurrence = f"workflow:{row['id']}:{row['next_run']}"
            if row["tool"] in {"notify", "social_publish"}:
                arguments["key"] = occurrence
            try:
                results.append(self.jobs.run(row["tool"], arguments,
                               occurrence,
                               lambda row=row, arguments=arguments: self.tools.execute(row["tool"], arguments)))
            except Exception:
                self.pause(row["id"], True)
                self.store.audit("workflow_error", {"id": row["id"], "tool": row["tool"]})
                results.append({"workflow_id": row["id"], "error": "Workflow paused after failure; inspect receipt"})
        return results

    def notification_deferred(self, arguments):
        if arguments.get("urgent", False):
            return False
        settings = self.tools.settings
        hour = datetime.now(ZoneInfo(settings.timezone)).hour
        start, end = settings.quiet_start, settings.quiet_end
        quiet = start <= hour < end if start < end else start > end and (hour >= start or hour < end)
        if quiet:
            return True
        if self.tools.focus is not None:
            session = self.tools.focus.status()["session"]
            return bool(session and session["status"] == "active")
        return False
