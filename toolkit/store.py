"""SQLite session metadata; file bytes never enter the database."""
import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

from .core import InputError, reader


class Store:
    def __init__(self, root: str, max_pages=500, max_mb=100):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_pages = max_pages
        self.max_bytes = max_mb * 1024 * 1024
        self.db = sqlite3.connect(self.root / "sessions.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS sessions (uid INTEGER PRIMARY KEY, touched REAL, state TEXT)")
        self.db.commit()

    def get(self, uid):
        row = self.db.execute("SELECT state FROM sessions WHERE uid=?", (uid,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, uid, state):
        self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)", (uid, time.time(), json.dumps(state)))
        self.db.commit()

    def directory(self, uid):
        path = self.root / str(int(uid))
        path.mkdir(exist_ok=True)
        return path

    def put(self, uid, data):
        directory = self.directory(uid)
        if sum(p.stat().st_size for p in directory.iterdir() if p.is_file()) + len(data) > self.max_bytes:
            raise InputError("Session storage limit reached. Finish or /cancel before adding more files.")
        if len(reader(data).pages) > self.max_pages:
            raise InputError(f"The page limit is {self.max_pages} pages.")
        name = uuid.uuid4().hex + ".pdf"
        (directory / name).write_bytes(data)
        return name

    def read(self, uid, name):
        if Path(name).name != name:
            raise InputError("Invalid session file.")
        return (self.directory(uid) / name).read_bytes()

    def commit(self, uid, state, data=None, name=None):
        new_file = self.put(uid, data) if data is not None else state["work"]
        state["history"].append([state["work"], state["name"]])
        state["work"] = new_file
        state["name"] = name or state["name"]
        state["mode"] = "menu"
        self.save(uid, state)

    def delete(self, uid):
        self.db.execute("DELETE FROM sessions WHERE uid=?", (uid,))
        self.db.commit()
        path = self.root / str(int(uid))
        if path.exists():
            shutil.rmtree(path)

    def expire(self, seconds):
        for (uid,) in self.db.execute("SELECT uid FROM sessions WHERE touched < ?", (time.time() - seconds,)).fetchall():
            self.delete(uid)
        # Recover files orphaned by a crash before a session was recorded.
        active = {str(row[0]) for row in self.db.execute("SELECT uid FROM sessions")}
        for path in self.root.iterdir():
            if path.is_dir() and path.name.isdecimal() and path.name not in active:
                shutil.rmtree(path)
