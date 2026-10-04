import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "worker.sqlite3"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                at TEXT NOT NULL, kind TEXT NOT NULL, message TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, vendor TEXT NOT NULL, issued TEXT NOT NULL,
                filename TEXT NOT NULL, unpaid INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger (
                source_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, invoice_number TEXT NOT NULL,
                amount_minor INTEGER NOT NULL CHECK(amount_minor > 0), currency TEXT NOT NULL,
                issued TEXT NOT NULL, due TEXT NOT NULL, source_hash TEXT NOT NULL,
                task_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
                UNIQUE(vendor, invoice_number));
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError("Task not found")
            result = dict(row)
            result["state"] = json.loads(result["state"])
            result["events"] = [dict(e) for e in db.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
            for event in result["events"]:
                event["detail"] = json.loads(event["detail"])
            return result

    def save(self, task_id, state, status="running"):
        with self.connect() as db:
            db.execute("UPDATE tasks SET state=?,status=?,updated_at=? WHERE id=?",
                       (json.dumps(state), status, now(), task_id))

    def event(self, task_id, kind, message, detail=None):
        with self.connect() as db:
            db.execute("INSERT INTO events(task_id,at,kind,message,detail) VALUES(?,?,?,?,?)",
                       (task_id, now(), kind, message, json.dumps(detail or {})))

    def ledger(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM ledger ORDER BY recorded_at DESC")]

