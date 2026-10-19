"""Background loop: ring timeouts, retries, stale calls and (optional) automatic daily calls."""
import logging
import threading
import time
from datetime import datetime, timedelta

from .. import caregiver, config, db
from . import orchestrator as O

log = logging.getLogger("saathi.scheduler")
_stop = threading.Event()
AUTO_CALL_WINDOW_H = 3
STALE_CALL_MIN = 5


def _parse(ts: str | None):
    return datetime.fromisoformat(ts) if ts else None


def tick() -> None:
    now = db.now_local()
    # 1) Ringing past deadline → unanswered attempt
    for s in db.q("SELECT id, ring_deadline FROM call_sessions WHERE status = 'ringing'"):
        dl = _parse(s["ring_deadline"])
        if dl and dl <= now:
            O.handle_no_answer(s["id"], "no_answer")
    # 2) Retry due → ring again
    for s in db.q("SELECT id, next_ring_at FROM call_sessions WHERE status = 'retry_wait'"):
        nr = _parse(s["next_ring_at"])
        if nr and nr <= now:
            O.ring_now(s["id"])
    # 3) Calls with no activity for a while → close as timed out
    for s in db.q("SELECT id FROM call_sessions WHERE status = 'in_progress'"):
        last = db.q1("SELECT MAX(created_at) AS t FROM call_turns WHERE session_id = ?", (s["id"],))
        lt = _parse(last["t"]) if last and last["t"] else None
        if lt and now - lt > timedelta(minutes=STALE_CALL_MIN):
            O.hangup(s["id"], by="timeout")
    # 4) Autonomous daily calls at each slot's medicine time
    for p in db.q("SELECT id FROM patients WHERE auto_calls = 1 AND is_test = 0"):
        for slot in ("morning", "evening"):
            meds = [m for m in caregiver.schedule(p["id"]) if m["slot"] == slot and m.get("time")]
            if not meds:
                continue
            hh, mm = map(int, min(m["time"] for m in meds).split(":"))
            start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (start <= now <= start + timedelta(hours=AUTO_CALL_WINDOW_H)):
                continue
            exists = db.q1("SELECT id FROM call_sessions WHERE patient_id = ? AND date = ? AND slot = ?",
                           (p["id"], db.today(), slot))
            if not exists:
                log.info("Auto check-in for %s (%s)", p["id"], slot)
                O.start_checkin(p["id"], slot, trigger="scheduler")


def _loop() -> None:
    while not _stop.is_set():
        try:
            tick()
        except Exception:
            log.exception("scheduler tick failed")
        _stop.wait(config.SCHEDULER_TICK_S)


def start() -> None:
    if config.SCHEDULER_ENABLED:
        _stop.clear()
        threading.Thread(target=_loop, name="saathi-scheduler", daemon=True).start()


def stop() -> None:
    _stop.set()
