"""SQLite persistence. One connection per call; WAL so the API reads while the worker writes."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, video_dir TEXT NOT NULL,
  created_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'new', summary TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  path TEXT NOT NULL, name TEXT NOT NULL, info TEXT, telemetry_source TEXT,
  stats TEXT, status TEXT NOT NULL DEFAULT 'new', error TEXT
);
CREATE TABLE IF NOT EXISTS selections (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  params TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', n_images INTEGER DEFAULT 0,
  summary TEXT, created_at REAL NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  selection_id INTEGER NOT NULL REFERENCES selections(id) ON DELETE CASCADE,
  parent_run_id INTEGER, preset TEXT NOT NULL, options TEXT NOT NULL, excluded TEXT,
  status TEXT NOT NULL DEFAULT 'queued', qa TEXT, created_at REAL NOT NULL, finished_at REAL,
  error TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT NOT NULL, ref_id INTEGER,
  params TEXT, status TEXT NOT NULL DEFAULT 'queued', progress REAL DEFAULT 0, stage TEXT,
  message TEXT, error TEXT, cancel INTEGER DEFAULT 0,
  created_at REAL NOT NULL, started_at REAL, finished_at REAL
);
"""

JSON_COLS = {"summary", "info", "stats", "params", "qa", "options", "excluded"}


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _alive(pid) -> bool:
    import os
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def init() -> None:
    with conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        if "worker_pid" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN worker_pid INTEGER")
        if "owner_pid" not in cols:
            c.execute("ALTER TABLE jobs ADD COLUMN owner_pid INTEGER")
        # queued jobs reserved for a process that no longer exists become claimable
        for r in c.execute("SELECT id, owner_pid FROM jobs WHERE status='queued' AND owner_pid IS NOT NULL").fetchall():
            if not _alive(r["owner_pid"]):
                c.execute("UPDATE jobs SET owner_pid=NULL WHERE id=?", (r["id"],))
        # a job is only "interrupted" if the process that ran it is gone; another
        # live process (web app and command line can run side by side) keeps its jobs
        now = time.time()
        dead = [r["id"] for r in c.execute("SELECT id, worker_pid FROM jobs WHERE status='running'")
                if not _alive(r["worker_pid"])]
        for jid in dead:
            j = c.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            c.execute("UPDATE jobs SET status='failed', error='interrupted (the program was stopped)', "
                      "finished_at=? WHERE id=?", (now, jid))
            table = {"reconstruct": "runs", "select": "selections", "pipeline": "selections",
                     "analyze": "projects"}.get(j["kind"])
            ref = j["project_id"] if j["kind"] == "analyze" else j["ref_id"]
            if table and ref:
                c.execute(f"UPDATE {table} SET status='failed', error='interrupted (the program was stopped)' "
                          f"WHERE id=? AND status IN ('running','queued','analyzing')", (ref,))


def row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k]:
            try:
                d[k] = json.loads(d[k])
            except (TypeError, ValueError):
                pass
    return d


def one(sql: str, args=()) -> dict | None:
    with conn() as c:
        return row(c.execute(sql, args).fetchone())


def all_(sql: str, args=()) -> list[dict]:
    with conn() as c:
        return [row(r) for r in c.execute(sql, args).fetchall()]


def execute(sql: str, args=()) -> int:
    with conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def update(table: str, id_: int, **fields) -> None:
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v) if k in JSON_COLS and v is not None and not isinstance(v, str) else v)
    with conn() as c:
        c.execute(f"UPDATE {table} SET {', '.join(cols)} WHERE id=?", (*vals, id_))


def insert(table: str, **fields) -> int:
    cols = list(fields)
    vals = [json.dumps(v) if k in JSON_COLS and v is not None and not isinstance(v, str) else v
            for k, v in fields.items()]
    with conn() as c:
        cur = c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        return cur.lastrowid
