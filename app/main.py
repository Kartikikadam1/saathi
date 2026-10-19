"""FastAPI app: Mock Caregiver Dashboard API, elder phone API, agent controls, test runner."""
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import caregiver, config, db, seed
from .agent import orchestrator as O, scheduler
from .agent.trace import trace
from .testing import scenarios

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed.ensure_seeded()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(title=config.APP_NAME, version="1.0.0", lifespan=lifespan,
              description="Autonomous medication check-in agent for elders with a mock caregiver dashboard. "
                          "All data is synthetic.")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(O.AgentError)
async def agent_error(_, exc: O.AgentError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


# ------------------------------------------------------------------ pages
@app.get("/", include_in_schema=False)
def page_dashboard():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/phone", include_in_schema=False)
def page_phone():
    return FileResponse(os.path.join(STATIC, "phone.html"))


@app.get("/api/health")
def health():
    return {"ok": True, "time": db.now_iso()}


@app.get("/api/config")
def get_config():
    return {"llm_enabled": config.llm_enabled(), "llm_model": config.GEMINI_MODEL if config.llm_enabled() else None,
            "telegram_enabled": bool(config.TELEGRAM_BOT_TOKEN), "ring_timeout_s": config.RING_TIMEOUT_S,
            "retry_interval_s": config.RETRY_INTERVAL_S, "max_retries": config.MAX_RETRIES,
            "timezone": str(config.TZ), "today": db.today(), "demo_patient_id": seed.DEMO_PATIENT_ID}


@app.get("/api/llm/check")
def llm_check():
    """Sends one sample sentence to Gemini so you can confirm the key and model work after deploying."""
    from .agent import llm
    if not config.llm_enabled():
        return {"ok": False, "reason": "GEMINI_API_KEY is not set — the agent is running on the rule engine."}
    meds = [{"id": "MED-001", "name": "Metformin", "dose": "500 mg", "aliases": []}]
    out = llm.classify("haan, sugar ki goli le li, bas thoda chakkar aa raha hai", meds, "dose", "Did you take Metformin?")
    return {"ok": out is not None, "model": config.GEMINI_MODEL, "result": out, "error": llm.last_error}


# ------------------------------------------------------------------ Mock Caregiver Dashboard API (agent data source)
def _patient_or_404(pid):
    p = caregiver.get_patient(pid)
    if not p:
        raise HTTPException(404, "Unknown patient")
    return p


@app.get("/api/caregiver/patients")
def list_patients(include_tests: bool = False):
    sql = "SELECT * FROM patients" + ("" if include_tests else " WHERE is_test = 0") + " ORDER BY id"
    return db.q(sql)


@app.get("/api/caregiver/patients/{pid}")
def patient_profile(pid: str):
    _patient_or_404(pid)
    return caregiver.profile(pid)


@app.get("/api/caregiver/patients/{pid}/schedule")
def patient_schedule(pid: str):
    _patient_or_404(pid)
    return caregiver.schedule(pid, include_inactive=True)


@app.get("/api/caregiver/patients/{pid}/contacts")
def patient_contacts(pid: str):
    _patient_or_404(pid)
    return caregiver.contacts(pid)


@app.get("/api/caregiver/patients/{pid}/consent")
def patient_consent(pid: str):
    _patient_or_404(pid)
    return caregiver.consent(pid)


class ConsentBody(BaseModel):
    call_consent: bool
    recording_consent: bool | None = None
    family_sharing_consent: bool | None = None
    granted_by: str | None = None


@app.put("/api/caregiver/patients/{pid}/consent")
def set_consent(pid: str, body: ConsentBody):
    _patient_or_404(pid)
    data = {"call_consent": int(body.call_consent),
            "recording_consent": int(body.call_consent if body.recording_consent is None else body.recording_consent),
            "family_sharing_consent": int(body.call_consent if body.family_sharing_consent is None else body.family_sharing_consent),
            "granted_by": body.granted_by or ("Caregiver via dashboard" if body.call_consent else None),
            "granted_at": db.now_iso() if body.call_consent else None,
            "notes": "Updated from dashboard"}
    db.update("consents", "patient_id", pid, data)
    return caregiver.consent(pid)


@app.get("/api/caregiver/patients/{pid}/logs")
def patient_logs(pid: str, days: int = Query(7, ge=1, le=60)):
    _patient_or_404(pid)
    return caregiver.logs(pid, days)


@app.get("/api/caregiver/rules")
def care_rules():
    return caregiver.rules()


class SettingsBody(BaseModel):
    auto_calls: bool | None = None
    language: str | None = Field(None, pattern="^(en|hi)$")


@app.put("/api/caregiver/patients/{pid}/settings")
def patient_settings(pid: str, body: SettingsBody):
    _patient_or_404(pid)
    data = {}
    if body.auto_calls is not None:
        data["auto_calls"] = int(body.auto_calls)
    if body.language:
        data["language"] = body.language
    if data:
        db.update("patients", "id", pid, data)
    return caregiver.get_patient(pid)


class ContactBody(BaseModel):
    telegram_chat_id: str | None = None


@app.put("/api/caregiver/contacts/{cid}")
def update_contact(cid: str, body: ContactBody):
    if not db.q1("SELECT id FROM family_contacts WHERE id = ?", (cid,)):
        raise HTTPException(404, "Unknown contact")
    db.update("family_contacts", "id", cid, {"telegram_chat_id": (body.telegram_chat_id or "").strip() or None})
    return db.q1("SELECT * FROM family_contacts WHERE id = ?", (cid,))


# ------------------------------------------------------------------ dashboard views
@app.get("/api/dashboard/{pid}/overview")
def overview(pid: str):
    p = _patient_or_404(pid)
    today = db.today()
    meds = caregiver.schedule(pid, include_inactive=True)
    active_meds = [m for m in meds if m["active"]]
    logs7 = caregiver.logs(pid, 7)
    today_logs = {(l["med_id"], l["slot"]): l for l in logs7 if l["date"] == today}
    slots = {}
    for slot in ("morning", "evening"):
        sess = db.q1("SELECT * FROM call_sessions WHERE patient_id=? AND date=? AND slot=? ORDER BY created_at DESC LIMIT 1",
                     (pid, today, slot))
        slots[slot] = {
            "meds": [{"id": m["id"], "name": m["name"], "dose": m["dose"], "time": m["time"],
                      "status": today_logs.get((m["id"], slot), {}).get("status", "pending"),
                      "log_id": today_logs.get((m["id"], slot), {}).get("id"),
                      "verified": today_logs.get((m["id"], slot), {}).get("verified")}
                     for m in active_meds if m["slot"] == slot],
            "session": sess,
        }
    days = [(db.now_local().date() - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    grid = {}
    for l in logs7:
        grid.setdefault(l["date"], {}).setdefault(l["slot"], []).append(l["status"])
    taken = sum(1 for l in logs7 if l["status"] == "taken")
    active = db.q1("SELECT * FROM call_sessions WHERE patient_id=? AND status IN ('ringing','retry_wait','in_progress') "
                   "ORDER BY created_at DESC LIMIT 1", (pid,))
    return {
        "patient": p, "consent": caregiver.consent(pid), "today": today, "slots": slots, "active_session": active,
        "adherence_7d_pct": round(100 * taken / len(logs7)) if logs7 else None,
        "grid": {"days": days, "data": grid},
        "open_alerts": db.q("SELECT * FROM alerts WHERE patient_id=? AND status='open' ORDER BY created_at DESC LIMIT 30", (pid,)),
        "pending_approvals": db.q("SELECT * FROM approvals WHERE patient_id=? AND status='pending' ORDER BY requested_at DESC", (pid,)),
        "recent_sessions": db.q("SELECT * FROM call_sessions WHERE patient_id=? ORDER BY created_at DESC LIMIT 12", (pid,)),
    }


@app.get("/api/sessions")
def sessions(patient_id: str | None = None, limit: int = Query(50, le=200)):
    if patient_id:
        return db.q("SELECT * FROM call_sessions WHERE patient_id=? ORDER BY created_at DESC LIMIT ?", (patient_id, limit))
    return db.q("SELECT * FROM call_sessions WHERE is_test=0 ORDER BY created_at DESC LIMIT ?", (limit,))


@app.get("/api/sessions/{sid}")
def session_detail(sid: str):
    s = O.get_session(sid)
    if not s:
        raise HTTPException(404, "Unknown session")
    return {
        "session": s, "patient": caregiver.get_patient(s["patient_id"]),
        "turns": db.q("SELECT * FROM call_turns WHERE session_id=? ORDER BY seq", (sid,)),
        "dose_logs": db.q("SELECT * FROM dose_logs WHERE source_session_id=?", (sid,)),
        "alerts": db.q("SELECT * FROM alerts WHERE session_id=? ORDER BY created_at", (sid,)),
        "approvals": db.q("SELECT * FROM approvals WHERE session_id=? ORDER BY requested_at", (sid,)),
        "notifications": db.q("SELECT n.* FROM notifications n JOIN alerts a ON a.id = n.alert_id WHERE a.session_id=? "
                              "ORDER BY n.created_at", (sid,)),
    }


@app.get("/api/sessions/{sid}/trace")
def session_trace(sid: str):
    if not O.get_session(sid):
        raise HTTPException(404, "Unknown session")
    return trace(sid)


@app.get("/api/alerts")
def alerts(patient_id: str | None = None, status: str | None = None):
    sql, params = "SELECT * FROM alerts WHERE 1=1", []
    if patient_id:
        sql += " AND patient_id = ?"; params.append(patient_id)
    else:
        sql += " AND patient_id NOT LIKE 'P-TEST%'"
    if status:
        sql += " AND status = ?"; params.append(status)
    return db.q(sql + " ORDER BY created_at DESC LIMIT 200", tuple(params))


@app.post("/api/alerts/{aid}/ack")
def ack_alert(aid: str):
    if not db.q1("SELECT id FROM alerts WHERE id=?", (aid,)):
        raise HTTPException(404, "Unknown alert")
    db.update("alerts", "id", aid, {"status": "acknowledged", "acknowledged_at": db.now_iso()})
    return db.q1("SELECT * FROM alerts WHERE id=?", (aid,))


@app.get("/api/approvals")
def approvals(patient_id: str | None = None, status: str | None = None):
    sql, params = "SELECT * FROM approvals WHERE 1=1", []
    if patient_id:
        sql += " AND patient_id = ?"; params.append(patient_id)
    else:
        sql += " AND patient_id NOT LIKE 'P-TEST%'"
    if status:
        sql += " AND status = ?"; params.append(status)
    return db.q(sql + " ORDER BY requested_at DESC LIMIT 200", tuple(params))


class DecideBody(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    by: str = "Caregiver"
    note: str = ""


@app.post("/api/approvals/{ap_id}/decide")
def decide(ap_id: str, body: DecideBody):
    return O.decide_approval(ap_id, body.decision, body.by, body.note)


@app.get("/api/notifications")
def notifications(patient_id: str | None = None):
    if patient_id:
        return db.q("SELECT * FROM notifications WHERE patient_id=? ORDER BY created_at DESC LIMIT 200", (patient_id,))
    return db.q("SELECT * FROM notifications WHERE patient_id NOT LIKE 'P-TEST%' ORDER BY created_at DESC LIMIT 200")


# ------------------------------------------------------------------ agent control
class StartBody(BaseModel):
    patient_id: str = seed.DEMO_PATIENT_ID
    slot: str = Field(pattern="^(morning|evening)$")


@app.post("/api/agent/checkin")
def start(body: StartBody):
    return O.start_checkin(body.patient_id, body.slot, trigger="manual")


@app.post("/api/agent/sessions/{sid}/simulate-no-answer")
def simulate_no_answer(sid: str):
    """Demo helper: treat the current ring as unanswered right now (instead of waiting for the timeout)."""
    return O.handle_no_answer(sid, "no_answer (simulated)")


@app.post("/api/agent/sessions/{sid}/retry-now")
def retry_now(sid: str):
    s = O.get_session(sid)
    if not s or s["status"] != "retry_wait":
        raise HTTPException(409, "Session is not waiting for a retry")
    return O.ring_now(sid)


# ------------------------------------------------------------------ elder phone (browser call)
@app.get("/api/phone/{pid}/state")
def phone_state(pid: str):
    _patient_or_404(pid)
    s = db.q1("SELECT * FROM call_sessions WHERE patient_id=? AND status IN ('ringing','retry_wait','in_progress') "
              "ORDER BY created_at DESC LIMIT 1", (pid,))
    if not s:
        s = db.q1("SELECT * FROM call_sessions WHERE patient_id=? AND ended_at IS NOT NULL ORDER BY ended_at DESC LIMIT 1", (pid,))
    out = {"patient": caregiver.get_patient(pid), "session": s, "turns": [], "ring_seconds_left": None}
    if s:
        out["turns"] = db.q("SELECT id, seq, speaker, text, lang, translation_en FROM call_turns WHERE session_id=? ORDER BY seq",
                            (s["id"],))
        if s["status"] == "ringing" and s["ring_deadline"]:
            from datetime import datetime
            out["ring_seconds_left"] = max(0, int((datetime.fromisoformat(s["ring_deadline"]) - db.now_local()).total_seconds()))
    return out


class AnswerBody(BaseModel):
    lang: str | None = Field(None, pattern="^(en|hi)$")


@app.post("/api/phone/sessions/{sid}/answer")
def phone_answer(sid: str, body: AnswerBody):
    return O.answer(sid, body.lang)


@app.post("/api/phone/sessions/{sid}/decline")
def phone_decline(sid: str):
    return O.handle_no_answer(sid, "declined")


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: str | None = Field(None, pattern="^(en|hi)$")


@app.post("/api/phone/sessions/{sid}/reply")
def phone_reply(sid: str, body: ReplyBody):
    return O.reply(sid, body.text, body.lang)


@app.post("/api/phone/sessions/{sid}/hangup")
def phone_hangup(sid: str):
    return O.hangup(sid, by="elder")


# ------------------------------------------------------------------ tests & admin
@app.get("/api/tests")
def test_definitions():
    return [{k: tc[k] for k in ("id", "official", "name", "input", "expected")} for tc in scenarios.SCENARIOS]


class RunBody(BaseModel):
    ids: list[str] | None = None


@app.post("/api/tests/run")
def run_tests(body: RunBody | None = None):
    return scenarios.run(body.ids if body else None)


@app.get("/api/tests/latest")
def latest_tests():
    return scenarios.latest_batch() or {"batch_id": None, "results": [], "passed": 0, "total": 0}


@app.get("/api/tests/latest/report.md", response_class=PlainTextResponse)
def latest_report():
    b = scenarios.latest_batch()
    if not b:
        raise HTTPException(404, "No test runs yet")
    return PlainTextResponse(scenarios.report_markdown(b), headers={"Content-Disposition": "attachment; filename=saathi-test-report.md"})


@app.post("/api/admin/reset")
def reset():
    seed.reset_demo()
    return {"ok": True}
