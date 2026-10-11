"""At-most-once dispatch receipts; uncertain effects require reconciliation."""
import hashlib
import json
import uuid

from .store import Store, now, redact


class JobConflict(ValueError):
    pass


def serialize(row):
    values = dict(row)
    values["result"] = json.loads(values["result"]) if values["result"] else None
    return {k: v for k, v in values.items() if k != "fingerprint"}


class JobService:
    def __init__(self, store: Store):
        self.store = store

    def reserve(self, tool: str, arguments: dict, key: str):
        if not key or len(key) > 128:
            raise ValueError("idempotency key must be 1..128 characters")
        fingerprint = hashlib.sha256(json.dumps([tool, arguments], sort_keys=True).encode()).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise JobConflict("idempotency key already belongs to a different request")
                return serialize(row), False
            job_id, timestamp = str(uuid.uuid4()), now()
            db.execute("INSERT INTO jobs(id,key,tool,fingerprint,status,result,error,created,updated) "
                       "VALUES(?,?,?,?,?,?,?,?,?)",
                       (job_id, key, tool, fingerprint, "queued", None, None, timestamp, timestamp))
            # Explicit columns avoid schema-order assumptions in future migrations.
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return serialize(row), True

    def claim(self, job_id: str):
        with self.store.connect() as db:
            return db.execute("UPDATE jobs SET status='running',updated=? WHERE id=? AND status='queued'",
                              (now(), job_id)).rowcount == 1

    def get(self, job_id: str):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return serialize(row) if row else None

    def list(self, limit=100):
        with self.store.connect() as db:
            return [serialize(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (min(max(limit, 1), 500),))]

    def run(self, tool: str, arguments: dict, key: str, handler):
        job, _ = self.reserve(tool, arguments, key)
        if job["status"] == "succeeded":
            return {"result": job["result"], "job": job}
        if job["status"] != "queued" or not self.claim(job["id"]):
            raise JobConflict("job already dispatched; inspect its receipt before retrying")
        try:
            result = handler()
            encoded = json.dumps(redact(result), ensure_ascii=False)
            if len(encoded) > 3_000_000:
                raise ValueError("tool result exceeds storage limit")
        except Exception:
            # Details from untrusted provider exceptions can contain credentials.
            with self.store.connect() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?",
                           ("Execution failed; inspect tool input and capability status", now(), job["id"]))
            self.store.audit("job_failed", {"id": job["id"], "tool": tool})
            raise
        with self.store.connect() as db:
            db.execute("UPDATE jobs SET status='succeeded',result=?,updated=? WHERE id=?",
                       (encoded, now(), job["id"]))
        self.store.audit("job_succeeded", {"id": job["id"], "tool": tool})
        return {"result": result, "job": self.get(job["id"])}

    def recover(self):
        with self.store.connect() as db:
            count = db.execute("UPDATE jobs SET status='uncertain',error=?,updated=? WHERE status='running'",
                               ("Interrupted execution; reconcile resulting state before retrying", now())).rowcount
        if count:
            self.store.audit("recovery", {"uncertain_jobs": count})
        return count
