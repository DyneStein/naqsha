"""Single background worker draining a SQLite job queue.

One job at a time on purpose: ODM saturates every core and most of the RAM,
and frame export saturates the video decoder.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import traceback
from typing import Callable

from . import db

log = logging.getLogger("naqsha.jobs")


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: dict):
        self.job = job
        self.id = job["id"]
        self._last = 0.0
        self.on_cancel: list[Callable[[], None]] = []

    def progress(self, frac: float, stage: str | None = None, message: str | None = None,
                 force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last < 0.5 and stage is None:
            return
        self._last = now
        fields = {"progress": round(max(0.0, min(1.0, frac)), 4)}
        if stage is not None:
            fields["stage"] = stage
        if message is not None:
            fields["message"] = message
        db.update("jobs", self.id, **fields)
        self.check_cancel()

    def check_cancel(self) -> None:
        r = db.one("SELECT cancel FROM jobs WHERE id=?", (self.id,))
        if r and r["cancel"]:
            raise Cancelled()

    def sub(self, start: float, end: float, stage: str | None = None):
        """Progress callback mapping [0,1] into [start,end] of the overall job."""
        def cb(f: float, message: str | None = None):
            self.progress(start + (end - start) * f, stage, message)
        return cb


HANDLERS: dict[str, Callable[[JobContext], None]] = {}
# When set (command-line runs), jobs submitted by this process are reserved for
# it, so a web server sharing the database does not pick them up.
OWNER: int | None = None


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def submit(kind: str, project_id: int | None, ref_id: int | None = None, params: dict | None = None) -> int:
    jid = db.insert("jobs", kind=kind, project_id=project_id, ref_id=ref_id, params=params or {},
                    status="queued", created_at=time.time(), owner_pid=OWNER)
    _wake.set()
    return jid


def cancel(job_id: int) -> None:
    db.update("jobs", job_id, cancel=1)
    j = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if j and j["status"] == "queued":
        db.update("jobs", job_id, status="cancelled", finished_at=time.time())
    ctx = _running.get(job_id)
    if ctx:
        for fn in ctx.on_cancel:
            try:
                fn()
            except Exception:
                log.exception("cancel hook failed")


_wake = threading.Event()
_running: dict[int, JobContext] = {}


def _loop() -> None:
    while True:
        if OWNER is None:
            job = db.one("SELECT * FROM jobs WHERE status='queued' AND owner_pid IS NULL ORDER BY id LIMIT 1")
        else:
            job = db.one("SELECT * FROM jobs WHERE status='queued' AND owner_pid=? ORDER BY id LIMIT 1", (OWNER,))
        if job is None:
            _wake.wait(2.0)
            _wake.clear()
            continue
        # claim atomically: another worker process may share this database
        with db.conn() as c:
            claimed = c.execute("UPDATE jobs SET status='running', started_at=?, progress=0, worker_pid=? "
                                "WHERE id=? AND status='queued'", (time.time(), os.getpid(), job["id"])).rowcount
        if not claimed:
            continue
        ctx = JobContext(job)
        _running[job["id"]] = ctx
        try:
            fn = HANDLERS[job["kind"]]
            fn(ctx)
            db.update("jobs", job["id"], status="done", progress=1.0, finished_at=time.time())
        except Cancelled:
            db.update("jobs", job["id"], status="cancelled", finished_at=time.time())
        except Exception as e:
            log.exception("job %s failed", job["id"])
            db.update("jobs", job["id"], status="failed", error=f"{e}\n\n{traceback.format_exc()[-3000:]}",
                      finished_at=time.time())
        finally:
            _running.pop(job["id"], None)


def start_worker() -> None:
    threading.Thread(target=_loop, name="naqsha-worker", daemon=True).start()
