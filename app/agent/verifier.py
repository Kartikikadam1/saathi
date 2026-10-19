"""Result Verification Engine: after every action, re-read what was written and compare it
with what the agent intended (target date, slot, status, source). Results go to the trace."""
from .. import caregiver, db
from .trace import audit


def _record(session_id, pid, check, expected, actual, passed, source_ids, source_urls):
    audit(session_id, pid, "VERIFY", check, f"{'PASS' if passed else 'FAIL'} — {check}",
          input={"expected": expected}, output={"actual": actual, "passed": passed},
          source_ids=source_ids, source_urls=source_urls)
    return passed


def verify_dose_log(session: dict, med_id: str, expected_status: str) -> bool:
    pid = session["patient_id"]
    row = db.q1("SELECT * FROM dose_logs WHERE patient_id=? AND med_id=? AND date=? AND slot=?",
                (pid, med_id, session["date"], session["slot"]))
    expected = {"med_id": med_id, "date": session["date"], "slot": session["slot"], "status": expected_status,
                "source_session_id": session["id"]}
    actual = {k: row[k] for k in expected} if row else None
    passed = actual == expected
    if passed:
        db.update("dose_logs", "id", row["id"], {"verified": 1})
    return _record(session["id"], pid, "verify_dose_log", expected, actual, passed,
                   [row["id"]] if row else [med_id],
                   [caregiver.url("logs", pid, row["id"])] if row else [caregiver.url("schedule", pid, med_id)])


def verify_notifications(session_id: str | None, pid: str, alert_id: str, expect_min: int) -> bool:
    rows = db.q("SELECT id, status FROM notifications WHERE alert_id = ?", (alert_id,))
    sent = [r for r in rows if r["status"] == "sent"]
    passed = len(sent) >= expect_min
    return _record(session_id, pid, "verify_notifications", {"alert_id": alert_id, "min_sent": expect_min},
                   {"sent": len(sent), "total": len(rows)}, passed,
                   [alert_id] + [r["id"] for r in rows], [caregiver.url("alert", rec_id=alert_id)])


def verify_session_complete(session: dict) -> bool:
    pid = session["patient_id"]
    due = session["due_med_ids"] or []
    rows = db.q("SELECT id, med_id, status FROM dose_logs WHERE patient_id=? AND date=? AND slot=?",
                (pid, session["date"], session["slot"]))
    logged = {r["med_id"]: r["status"] for r in rows}
    missing = [m for m in due if m not in logged]
    passed = not missing
    return _record(session["id"], pid, "verify_session_complete",
                   {"date": session["date"], "slot": session["slot"], "every_due_med_has_a_log": due},
                   {"logged": logged, "missing": missing}, passed,
                   [r["id"] for r in rows], [caregiver.url("logs", pid, r["id"]) for r in rows])


def verify_med_active(session_id, pid, med_id, expected_active: int) -> bool:
    row = db.q1("SELECT id, active FROM medications WHERE id = ?", (med_id,))
    passed = bool(row) and row["active"] == expected_active
    return _record(session_id, pid, "verify_schedule_update", {"med_id": med_id, "active": expected_active},
                   {"active": row["active"] if row else None}, passed, [med_id], [caregiver.url("schedule", pid, med_id)])
