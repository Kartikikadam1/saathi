"""Mock Caregiver Dashboard data source.

The same functions back the public REST API (/api/caregiver/...) and the agent's
'Collect Details' / 'Find Information' tools, so every value the agent uses carries the
record's source_id and the URL where a human can inspect it.
"""
from datetime import date as _date, timedelta

import httpx

from . import config, db


def url(kind: str, pid: str | None = None, rec_id: str | None = None) -> str:
    base = {
        "patient": f"/api/caregiver/patients/{pid}",
        "schedule": f"/api/caregiver/patients/{pid}/schedule",
        "contacts": f"/api/caregiver/patients/{pid}/contacts",
        "consent": f"/api/caregiver/patients/{pid}/consent",
        "logs": f"/api/caregiver/patients/{pid}/logs",
        "rules": "/api/caregiver/rules",
        "session": f"/api/sessions/{rec_id}",
        "alert": "/api/alerts",
        "approval": "/api/approvals",
        "notification": "/api/notifications",
    }[kind]
    if kind == "session":
        return base
    return f"{base}#{rec_id}" if rec_id else base


def _tag(rec: dict, kind: str, pid: str | None) -> dict:
    rec = dict(rec)
    rec["source_id"] = rec["id"]
    rec["source_url"] = url(kind, pid, rec["id"])
    return rec


def get_patient(pid: str) -> dict | None:
    return db.q1("SELECT * FROM patients WHERE id = ?", (pid,))


def schedule(pid: str, include_inactive: bool = False) -> list[dict]:
    sql = "SELECT * FROM medications WHERE patient_id = ?" + ("" if include_inactive else " AND active = 1")
    rows = db.q(sql + " ORDER BY slot DESC, time, id", (pid,))
    return [_tag(r, "schedule", pid) for r in rows]


def contacts(pid: str) -> list[dict]:
    return [_tag(r, "contacts", pid) for r in db.q(
        "SELECT * FROM family_contacts WHERE patient_id = ? ORDER BY priority, id", (pid,))]


def consent(pid: str) -> dict | None:
    r = db.q1("SELECT * FROM consents WHERE patient_id = ?", (pid,))
    return _tag(r, "consent", pid) if r else None


def rules() -> list[dict]:
    return [_tag(r, "rules", None) for r in db.q("SELECT * FROM care_rules ORDER BY id")]


def rules_map() -> dict:
    return {r["key"]: r for r in rules()}


def logs(pid: str, days: int = 7, until: str | None = None) -> list[dict]:
    end = _date.fromisoformat(until) if until else db.now_local().date()
    start = (end - timedelta(days=days - 1)).isoformat()
    rows = db.q("SELECT * FROM dose_logs WHERE patient_id = ? AND date >= ? AND date <= ? ORDER BY date DESC, slot, med_id",
                (pid, start, end.isoformat()))
    return [_tag(r, "logs", pid) for r in rows]


def profile(pid: str) -> dict | None:
    p = get_patient(pid)
    if not p:
        return None
    p = _tag(p, "patient", pid)
    return {
        "patient": p,
        "medications": schedule(pid, include_inactive=True),
        "contacts": contacts(pid),
        "consent": consent(pid),
        "rules": rules(),
    }


def fetch_profile(pid: str) -> tuple[dict | None, str]:
    """Used by the agent. Returns (profile, how) — over HTTP when CAREGIVER_API_BASE is set."""
    if config.CAREGIVER_API_BASE:
        try:
            r = httpx.get(f"{config.CAREGIVER_API_BASE}/api/caregiver/patients/{pid}", timeout=8)
            r.raise_for_status()
            return r.json(), f"HTTP GET {config.CAREGIVER_API_BASE}/api/caregiver/patients/{pid}"
        except Exception as e:  # fall back to local read, but record it
            return profile(pid), f"local read (HTTP fetch failed: {e})"
    return profile(pid), f"GET /api/caregiver/patients/{pid} (in-process)"
