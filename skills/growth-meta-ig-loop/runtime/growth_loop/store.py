import contextlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .errors import WorkflowError
from .files import canonical, redact


class Store:
    def __init__(self, root, clock=time.time):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.path = self.root / "workflow.sqlite3"
        self.db = sqlite3.connect(str(self.path), isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, config TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
          key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
          cycle INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
          depends TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'ready',
          attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
          lease TEXT, lease_until REAL, outcome TEXT, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS operations (
          key TEXT PRIMARY KEY, task_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
          state TEXT NOT NULL, request TEXT NOT NULL, result TEXT, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
          task_key TEXT, event TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifacts (
          key TEXT PRIMARY KEY, run_id TEXT NOT NULL, cycle INTEGER NOT NULL,
          kind TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL
        );
        """)
        os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        else:
            self.db.execute("COMMIT")

    def event(self, run_id, task_key, event, data=None):
        self.db.execute(
            "INSERT INTO events(run_id,task_key,event,data,created) VALUES(?,?,?,?,?)",
            (run_id, task_key, event, canonical(redact(data or {})), self.clock()),
        )

    def create_run(self, config, run_id=None):
        run_id = run_id or uuid.uuid4().hex[:16]
        config = dict(config)
        config["_run_id"] = run_id
        self.db.execute("INSERT INTO runs VALUES(?,?,?)", (run_id, canonical(config), self.clock()))
        self.event(run_id, None, "run_created", {"mode": config.get("mode", "prepare")})
        return run_id

    def config(self, run_id):
        row = self.db.execute("SELECT config FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown run")
        return json.loads(row[0])

    def update_config(self, run_id, config, created_account_operation=None):
        config = dict(config)
        config["_run_id"] = run_id
        old = self.config(run_id)
        if old.get("brand", {}).get("url") != config.get("brand", {}).get("url"):
            raise WorkflowError("同一工作不能換成其他品牌；請建立獨立工作。", "scope_mismatch")
        writes = self.db.execute("SELECT count(*) FROM operations WHERE task_key LIKE ? AND state IN ('in_flight','uncertain','succeeded')", (run_id + ":%",)).fetchone()[0]
        writes += self.db.execute("SELECT count(*) FROM artifacts WHERE run_id=? AND (kind IN ('meta_ads','instagram') OR kind LIKE 'browser_receipt_publish_%')", (run_id,)).fetchone()[0]
        if writes:
            if old.get("bindings", {}) != config.get("bindings", {}):
                op = self.operation(created_account_operation) if created_account_operation else None
                allowed = False
                if op and op["state"] == "succeeded" and op["task_key"].startswith(run_id + ":") and op["request"].get("action") == "create_marketing_account":
                    channel = op["request"]["channel"]
                    expected = json.loads(json.dumps(old.get("bindings", {})))
                    if not expected.get(channel, {}).get("account_ref") and op["result"].get("created_account_ref"):
                        expected[channel]["account_ref"] = op["result"]["created_account_ref"]
                        allowed = expected == config.get("bindings", {})
                if not allowed:
                    raise WorkflowError("已有外部操作的工作不能改用其他渠道身分。", "scope_mismatch")
            for field in ("ad_account_id", "page_id", "ig_user_id", "business_id"):
                before = old.get("accounts", {}).get(field)
                after = config.get("accounts", {}).get(field)
                if before and before != after:
                    raise WorkflowError("已有外部操作的流程不能切換帳號；另建明確授權的新流程。", "scope_mismatch")
            if old.get("browser", {}).get("instagram_profile_url") != config.get("browser", {}).get("instagram_profile_url"):
                raise WorkflowError("已發布的流程不能切換IG品牌頁。", "scope_mismatch")
        self.db.execute("UPDATE runs SET config=? WHERE id=?", (canonical(config), run_id))

    def assert_lease(self, task, extend_seconds=180):
        if (self.root / '.transfer.lock').exists():
            raise WorkflowError('工作區已為搬移凍結，不再執行外部操作。','waiting_capability')
        now = self.clock()
        cursor = self.db.execute(
            "UPDATE tasks SET lease_until=? WHERE key=? AND state='running' AND lease=? AND lease_until>?",
            (now + extend_seconds, task["key"], task["lease"], now),
        )
        if cursor.rowcount != 1:
            raise WorkflowError("Lease lost before external operation", "lease_lost")

    def enqueue(self, run_id, cycle, kind, data=None, depends=(), next_at=0):
        key = "%s:%s:%s" % (run_id, cycle, kind)
        self.db.execute(
            "INSERT OR IGNORE INTO tasks(key,run_id,cycle,kind,data,depends,next_at,updated) VALUES(?,?,?,?,?,?,?,?)",
            (key, run_id, cycle, kind, canonical(data or {}), canonical(list(depends)), next_at, self.clock()),
        )
        return key

    def task(self, key):
        row = self.db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("data", "depends", "outcome"):
            result[field] = json.loads(result[field]) if result[field] else None
        return result

    def claim(self, run_id, lease_seconds=180):
        now = self.clock()
        if (self.root / '.transfer.lock').exists():
            return None
        if self.artifact(run_id, 0, "graph.stopped"):
            return None
        with self.transaction():
            if (self.root / '.transfer.lock').exists():
                return None
            expired = self.db.execute(
                "SELECT key FROM tasks WHERE run_id=? AND state='running' AND lease_until<=?", (run_id, now)
            ).fetchall()
            for row in expired:
                self.db.execute("UPDATE tasks SET state='ready',lease=NULL,lease_until=NULL WHERE key=?", (row[0],))
                self.event(run_id, row[0], "expired_lease_recovered")
            rows = self.db.execute(
                "SELECT key,depends FROM tasks WHERE run_id=? AND state IN ('ready','retry') AND next_at<=? ORDER BY cycle, rowid",
                (run_id, now),
            ).fetchall()
            for row in rows:
                dependencies = json.loads(row["depends"])
                if any(self.task(key) is None or self.task(key)["state"] != "done" for key in dependencies):
                    continue
                lease = uuid.uuid4().hex
                self.db.execute(
                    "UPDATE tasks SET state='running',lease=?,lease_until=?,attempts=attempts+1,updated=? WHERE key=?",
                    (lease, now + lease_seconds, now, row["key"]),
                )
                self.event(run_id, row["key"], "task_claimed", {"lease_until": now + lease_seconds})
                return self.task(row["key"])
        return None

    def finish(self, task, state, outcome, next_at=None):
        with self.transaction():
            cursor = self.db.execute(
                "UPDATE tasks SET state=?,outcome=?,next_at=?,lease=NULL,lease_until=NULL,updated=? WHERE key=? AND state='running' AND lease=?",
                (state, canonical(redact(outcome)), next_at or 0, self.clock(), task["key"], task["lease"]),
            )
            if cursor.rowcount != 1:
                raise WorkflowError("Lease is no longer owned; refusing stale completion", "lease_lost")
            self.event(task["run_id"], task["key"], "task_" + state, outcome)

    def resume_waiting(self, run_id):
        self.db.execute(
            "UPDATE tasks SET state='ready',next_at=0,updated=? WHERE run_id=? AND state IN ('waiting_owner','waiting_capability','waiting_approval')",
            (self.clock(), run_id),
        )
        self.event(run_id, None, "waiting_tasks_resumed")

    def artifact(self, run_id, cycle, kind, data=None):
        key = "%s:%s:%s" % (run_id, cycle, kind)
        if data is not None:
            self.db.execute(
                "INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?,?)",
                (key, run_id, cycle, kind, canonical(redact(data)), self.clock()),
            )
            return data
        row = self.db.execute("SELECT data FROM artifacts WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def operation(self, key):
        row = self.db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("request", "result"):
            result[field] = json.loads(result[field]) if result[field] else None
        return result

    def prepare_operation(self, key, task_key, fingerprint, request):
        with self.transaction():
            old = self.operation(key)
            if old and old["fingerprint"] != fingerprint:
                raise WorkflowError("Operation key reused with different payload", "payload_changed")
            if not old:
                self.db.execute(
                    "INSERT INTO operations VALUES(?,?,?,?,?,?,?)",
                    (key, task_key, fingerprint, "prepared", canonical(redact(request)), None, self.clock()),
                )
            return self.operation(key)

    def set_operation(self, key, state, result=None):
        self.db.execute(
            "UPDATE operations SET state=?,result=?,updated=? WHERE key=?",
            (state, canonical(redact(result)) if result is not None else None, self.clock(), key),
        )

    def status(self, run_id):
        from .continuity import summary
        tasks = [self.task(r[0]) for r in self.db.execute("SELECT key FROM tasks WHERE run_id=? ORDER BY cycle,rowid", (run_id,))]
        return {
            "run_id": run_id,
            "mode": self.config(run_id).get("mode", "prepare"),
            "pursuit": summary(self,run_id),
            "tasks": [{k: t[k] for k in ("key", "cycle", "kind", "state", "attempts", "next_at", "outcome")} for t in tasks],
        }
