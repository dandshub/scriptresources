"""Background thread: fires maintenance schedules and times out stuck sessions."""
from __future__ import annotations

import datetime as dt
import logging
import threading

from . import config, engine, ninjaone
from .db import DB, now

log = logging.getLogger("macstate.scheduler")


def due_schedules(db: DB, when: dt.datetime) -> list[dict]:
    today = when.strftime("%Y-%m-%d")
    hhmm = when.strftime("%H:%M")
    out = []
    for s in db.all("SELECT * FROM schedules WHERE enabled=1"):
        if str(when.weekday()) not in s["days"]:
            continue
        if s["last_run"] == today or hhmm < s["time"]:
            continue
        out.append(s)
    return out


def run_schedule(db: DB, sched: dict, created_by: str = "scheduler") -> int:
    count = 0
    for c in engine.target_computers(db, sched["target_type"], sched["target_id"]):
        engine.create_session(db, c, "scheduled", sched["mode"], created_by=f"{created_by}: {sched['name']}")
        count += 1
    return count


def tick(db: DB, when: dt.datetime | None = None) -> None:
    when = when or dt.datetime.now()
    for s in due_schedules(db, when):
        n = run_schedule(db, s)
        db.run("UPDATE schedules SET last_run=? WHERE id=?", (when.strftime("%Y-%m-%d"), s["id"]))
        log.info("schedule %s queued %d session(s)", s["name"], n)

    if ninjaone.sync_due(db):
        try:
            ninjaone.sync(db)
        except Exception as e:  # recorded in settings by sync()
            log.warning("NinjaOne sync failed: %s", e)

    cutoff = now() - config.SESSION_TIMEOUT
    for s in db.all("SELECT * FROM sessions WHERE status='running' AND COALESCE(updated_at,started_at) < ?",
                    (cutoff,)):
        db.run("UPDATE session_actions SET status='failed', message='session timed out' "
               "WHERE session_id=? AND status='running'", (s["id"],))
        engine.log(db, s["id"], None, "Session timed out waiting for the agent")
        engine.finish_session(db, s)


class Scheduler(threading.Thread):
    def __init__(self, db: DB, interval: float = 30.0) -> None:
        super().__init__(daemon=True, name="macstate-scheduler")
        self.db = db
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                tick(self.db)
            except Exception:  # keep the thread alive
                log.exception("scheduler tick failed")
