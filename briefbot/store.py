"""SQLite persistence. Each user action and its deduplication record commit together."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from .scenario import build_config


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, directory, journal_mode="WAL"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "uploads").mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / "briefbot.sqlite3", check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if journal_mode not in {"WAL", "DELETE"}:
            raise ValueError("Unsupported SQLite journal mode")
        self.db.execute("PRAGMA journal_mode=" + journal_mode)
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        PRAGMA busy_timeout=5000;
        CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS leads(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, draft TEXT NOT NULL UNIQUE,
            body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS leads_created ON leads(created DESC);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, lead_id TEXT NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, error TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
            path TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, action TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS webhook_receipts(update_id INTEGER PRIMARY KEY, status TEXT NOT NULL, updated REAL NOT NULL);
        """)
        if not self.db.execute("SELECT 1 FROM config LIMIT 1").fetchone():
            self.db.execute("INSERT INTO config(body,created) VALUES(?,?)", (dumps(build_config()), time.time()))
        # An interrupted external send has an unknown result. Never silently retry it.
        self.db.execute("UPDATE outbox SET status='review',error='interrupted_send' WHERE status='sending'")

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def config(self, version=None):
        with self.lock:
            row = self.db.execute("SELECT * FROM config WHERE id=?" if version else "SELECT * FROM config ORDER BY id DESC LIMIT 1",
                                  (version,) if version else ()).fetchone()
            return row["id"], json.loads(row["body"])

    def save_config(self, body, expected):
        with self.transaction() as db:
            current = db.execute("SELECT MAX(id) FROM config").fetchone()[0]
            if expected != current:
                raise ValueError("Настройки уже изменены. Обновите страницу и повторите правку.")
            cur = db.execute("INSERT INTO config(body,created) VALUES(?,?)", (dumps(body), time.time()))
            db.execute("INSERT INTO audit(action,created) VALUES('config_saved',?)", (time.time(),))
            return cur.lastrowid

    def leads(self):
        with self.lock:
            return [{**json.loads(r["body"]), "status": r["status"], "created": r["created"],
                     "delivery": (self.db.execute("SELECT status FROM outbox WHERE lead_id=?", (r["id"],)).fetchone() or ["local"])[0]}
                    for r in self.db.execute("SELECT * FROM leads ORDER BY created DESC")]

    def close(self):
        with self.lock:
            self.db.close()
