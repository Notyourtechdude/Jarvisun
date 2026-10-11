"""At-most-once inbound update claims; no messages or raw bot bindings persist."""
import hashlib


class PollCursor:
    def __init__(self, store, binding):
        self.store = store
        self.binding = hashlib.sha256(binding.encode("utf-8")).hexdigest()
        with store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS poll_cursors (binding TEXT PRIMARY KEY, offset INTEGER NOT NULL)")

    def offset(self):
        with self.store.connect() as db:
            row = db.execute("SELECT offset FROM poll_cursors WHERE binding=?", (self.binding,)).fetchone()
            return row[0] if row else 0

    def claim(self, update_id):
        if type(update_id) is not int or not 0 <= update_id < 2**63 - 1:
            raise ValueError("invalid messaging update identifier")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT offset FROM poll_cursors WHERE binding=?", (self.binding,)).fetchone()
            if row and update_id < row[0]:
                return False
            db.execute("INSERT INTO poll_cursors(binding,offset) VALUES(?,?) "
                       "ON CONFLICT(binding) DO UPDATE SET offset=excluded.offset",
                       (self.binding, update_id + 1))
        return True
