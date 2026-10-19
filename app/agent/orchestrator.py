"""The agent. Runs the 4-stage execution model from the brief on every call and every reply:

  STEP 01 COLLECT  — load medicine schedule, family contacts and consent (with source IDs/URLs)
  STEP 02 PLAN     — check consent, decide what is due, classify each reply, decide actions
  STEP 03 FIND     — query caregiver records: past logs, care rules, consecutive misses
  STEP 04 ACT      — log doses, raise alerts, notify family; irreversible actions wait for approval
  + VERIFY         — re-read every write and compare with the intended result
  + GUARDRAIL      — medical-advice block on input, safety check on every spoken line

Every step is written to the audit trail (trace.py) so the dashboard can show end-to-end evidence.
"""
import re
import threading
from datetime import datetime, timedelta

from .. import caregiver, config, db
from . import classifier, llm, notifier, responses as R, rules, verifier
from .trace import audit

ACTIVE = ("ringing", "retry_wait", "in_progress")
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


class AgentError(Exception):
    pass


def _lock(sid: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(sid, threading.RLock())


# ----------------------------------------------------------------------------- helpers

def get_session(sid: str) -> dict | None:
    return db.q1("SELECT * FROM call_sessions WHERE id = ?", (sid,))


def _save(sid: str, **fields) -> None:
    db.update("call_sessions", "id", sid, fields)


def _flags(s: dict) -> dict:
    f = s.get("flags") or {}
    f.setdefault("med_status", {})
    f.setdefault("alerted_symptoms", [])
    f.setdefault("concern_raised", False)
    f.setdefault("doctor_share_requested", False)
    return f


def _turns(sid: str) -> list[dict]:
    return db.q("SELECT * FROM call_turns WHERE session_id = ? ORDER BY seq", (sid,))


def _add_turn(sid, speaker, text, lang, translation_en=None, corrected=None, classification=None) -> dict:
    seq = (db.q1("SELECT COALESCE(MAX(seq),0) AS m FROM call_turns WHERE session_id = ?", (sid,))["m"] or 0) + 1
    row = {"id": db.new_id("TN"), "session_id": sid, "seq": seq, "speaker": speaker, "text": text, "lang": lang,
           "translation_en": translation_en, "corrected_text": corrected, "classification": classification or {},
           "created_at": db.now_iso()}
    db.insert("call_turns", row)
    return row


def _ctx(s: dict) -> dict:
    pid = s["patient_id"]
    p = caregiver.get_patient(pid)
    all_meds = {m["id"]: m for m in caregiver.schedule(pid, include_inactive=True)}
    due = [all_meds[m] for m in (s.get("due_med_ids") or []) if m in all_meds]
    contacts = caregiver.contacts(pid)
    first = lambda n: n.split()[0]
    return {
        "patient": p, "name": first(p["name"]), "due": due, "contacts": contacts,
        "family": first(contacts[0]["name"]) if contacts else "your family",
        "family_all": R._join([first(c["name"]) for c in contacts], "en") or "your family",
        "doctor": p.get("doctor_name") or "your doctor", "rules": caregiver.rules_map(),
    }


def _rule_src(rmap: dict, *keys):
    ids = [rmap[k]["id"] for k in keys if k in rmap]
    return ids, [caregiver.url("rules", rec_id=i) for i in ids]


def _speak(s: dict, text: str, text_en: str, lang: str) -> dict:
    """Output guardrail: every line is checked before it is spoken."""
    ok_en, hits_en = rules.agent_speech_is_safe(text_en)
    ok_l, hits_l = rules.agent_speech_is_safe(text)
    if not (ok_en and ok_l):
        ctx = _ctx(s)
        text, text_en = R.advice_refusal(lang, ctx["doctor"], ctx["family"])
    audit(s["id"], s["patient_id"], "GUARDRAIL", "output_safety_check",
          "Agent line passed the no-medical-advice check" if ok_en and ok_l else "Unsafe line blocked and replaced",
          input={"text_en": text_en}, output={"passed": ok_en and ok_l, "matched": hits_en + hits_l})
    return _add_turn(s["id"], "agent", text, lang, translation_en=text_en)


def _consecutive_missed_slots(hist: list[dict], date: str, slot: str) -> int:
    by_slot: dict[tuple, list[str]] = {}
    for l in hist:
        by_slot.setdefault((l["date"], l["slot"]), []).append(l["status"])
    order = []
    d = datetime.fromisoformat(date).date()
    cur = (d, slot)
    for _ in range(14):  # walk back up to 7 days of slots
        dd, sl = cur
        cur = (dd, "morning") if sl == "evening" else (dd - timedelta(days=1), "evening")
        order.append((cur[0].isoformat(), cur[1]))
    n = 0
    for key in order:
        sts = by_slot.get(key)
        if sts and "missed" in sts:
            n += 1
        else:
            break
    return n


# ----------------------------------------------------------------------------- ACT tools

def _log_dose(s: dict, med_id: str, status: str, evidence: str, turn_id: str | None) -> None:
    pid = s["patient_id"]
    existing = db.q1("SELECT id FROM dose_logs WHERE patient_id=? AND med_id=? AND date=? AND slot=?",
                     (pid, med_id, s["date"], s["slot"]))
    data = {"status": status, "source_session_id": s["id"], "source_turn_id": turn_id, "evidence_text": evidence,
            "verified": 0, "updated_at": db.now_iso()}
    if existing:
        db.update("dose_logs", "id", existing["id"], data)
        log_id = existing["id"]
    else:
        log_id = db.new_id("DL")
        db.insert("dose_logs", {"id": log_id, "patient_id": pid, "med_id": med_id, "date": s["date"], "slot": s["slot"],
                                "created_at": db.now_iso(), **data})
    audit(s["id"], pid, "ACT", "log_dose_status", f"Logged {med_id} as '{status}' for {s['date']} {s['slot']}",
          input={"med_id": med_id, "status": status, "evidence": evidence},
          output={"dose_log_id": log_id},
          source_ids=[med_id, log_id] + ([turn_id] if turn_id else []),
          source_urls=[caregiver.url("schedule", pid, med_id), caregiver.url("logs", pid, log_id),
                       caregiver.url("session", rec_id=s["id"])])
    verifier.verify_dose_log(get_session(s["id"]), med_id, status)


def _alert(s: dict, type_: str, severity: str, message: str, evidence: dict, turn_id: str | None = None) -> dict:
    pid = s["patient_id"]
    row = {"id": db.new_id("AL"), "patient_id": pid, "session_id": s["id"], "type": type_, "severity": severity,
           "message": message, "evidence": {**evidence, "turn_id": turn_id, "session_id": s["id"]},
           "status": "open", "created_at": db.now_iso()}
    db.insert("alerts", row)
    audit(s["id"], pid, "ACT", "create_alert", f"{severity.upper()} alert: {type_}",
          input={"type": type_, "severity": severity}, output={"alert_id": row["id"], "message": message},
          source_ids=[row["id"]] + ([turn_id] if turn_id else []),
          source_urls=[caregiver.url("alert", rec_id=row["id"]), caregiver.url("session", rec_id=s["id"])])
    row["evidence"] = row["evidence"]
    sent = notifier.notify_family(row, s["id"])
    expected = len(caregiver.contacts(pid)) if severity in ("critical", "high") else (1 if severity == "medium" else 0)
    if expected:
        verifier.verify_notifications(s["id"], pid, row["id"], expected)
    return row


def _request_approval(s: dict, action_type: str, payload: dict, reason: str, rmap: dict) -> dict:
    row = {"id": db.new_id("AP"), "patient_id": s["patient_id"], "session_id": s["id"], "action_type": action_type,
           "payload": payload, "reason": reason, "status": "pending", "requested_at": db.now_iso()}
    db.insert("approvals", row)
    rids, rurls = _rule_src(rmap, "approval_required_actions")
    audit(s["id"], s["patient_id"], "ACT", "request_human_approval",
          f"'{action_type}' cannot be undone → queued for caregiver approval ({row['id']})",
          input=payload, output={"approval_id": row["id"], "status": "pending"},
          source_ids=[row["id"]] + rids, source_urls=[caregiver.url("approval", rec_id=row["id"])] + rurls)
    return row


# ----------------------------------------------------------------------------- lifecycle

def start_checkin(pid: str, slot: str, trigger: str = "manual", is_test: bool = False, ring: bool = True) -> dict:
    if slot not in ("morning", "evening"):
        raise AgentError("slot must be 'morning' or 'evening'")
    if not caregiver.get_patient(pid):
        raise AgentError(f"Unknown patient {pid}")
    active = db.q1(f"SELECT * FROM call_sessions WHERE patient_id = ? AND status IN {ACTIVE}", (pid,))
    if active:
        return active

    sid, date = db.new_id("CS"), db.today()
    p = caregiver.get_patient(pid)
    db.insert("call_sessions", {"id": sid, "patient_id": pid, "date": date, "slot": slot, "status": "planning",
                                "language": p.get("language") or "en", "trigger": trigger,
                                "is_test": 1 if is_test else 0, "created_at": db.now_iso(), "flags": {}})

    # STEP 01 — COLLECT
    prof, how = caregiver.fetch_profile(pid)
    meds = [m for m in prof["medications"] if m["active"]]
    due = [m for m in meds if m["slot"] == slot]
    audit(sid, pid, "COLLECT", "load_medicine_schedule",
          f"Loaded {len(meds)} active medicines; {len(due)} due this {slot}",
          input={"patient_id": pid, "slot": slot, "via": how},
          output={"due": [{"id": m["id"], "name": m["name"], "dose": m["dose"], "time": m["time"]} for m in due]},
          source_ids=[m["source_id"] for m in meds], source_urls=[m["source_url"] for m in meds])
    audit(sid, pid, "COLLECT", "load_family_contacts",
          f"Loaded {len(prof['contacts'])} family contacts (primary: {prof['contacts'][0]['name'] if prof['contacts'] else 'none'})",
          output={"contacts": [{"id": c["id"], "name": c["name"], "relation": c["relation"], "priority": c["priority"]}
                               for c in prof["contacts"]]},
          source_ids=[c["source_id"] for c in prof["contacts"]], source_urls=[c["source_url"] for c in prof["contacts"]])
    con = prof["consent"]
    audit(sid, pid, "COLLECT", "load_consent", "Loaded consent record",
          output={k: con[k] for k in ("call_consent", "recording_consent", "family_sharing_consent", "granted_by")} if con else {},
          source_ids=[con["source_id"]] if con else [], source_urls=[con["source_url"]] if con else [])

    # STEP 02 — PLAN
    s = get_session(sid)
    if not con or not con["call_consent"]:
        audit(sid, pid, "PLAN", "check_consent", "No call consent on record → the agent will NOT call",
              output={"decision": "blocked"}, source_ids=[con["source_id"]] if con else [],
              source_urls=[con["source_url"]] if con else [])
        _save(sid, status="blocked_no_consent", phase="ended", outcome="blocked_no_consent", ended_at=db.now_iso())
        _alert(get_session(sid), "consent_missing", "low",
               f"[Saathi] {p['name']}'s {slot} check-in was not placed because call consent is missing or revoked.",
               {"consent_id": con["id"] if con else None})
        return get_session(sid)
    if not due:
        audit(sid, pid, "PLAN", "plan_checkin", f"No active medicines due this {slot} → nothing to do")
        _save(sid, status="skipped", phase="ended", outcome="nothing_due", ended_at=db.now_iso())
        return get_session(sid)
    audit(sid, pid, "PLAN", "plan_checkin",
          f"Consent OK. Plan: call {p['name']} about {len(due)} {slot} medicine(s), confirm each dose, "
          f"check wellbeing, escalate on missed dose / symptoms / no answer",
          output={"due_med_ids": [m["id"] for m in due], "steps": ["ring", "greet", "confirm doses", "wellbeing", "log + notify", "verify"]},
          source_ids=[con["source_id"]] + [m["id"] for m in due])

    # STEP 03 — FIND
    rmap = caregiver.rules_map()
    hist = caregiver.logs(pid, days=7)
    consecutive = _consecutive_missed_slots(hist, date, slot)
    taken = sum(1 for l in hist if l["status"] == "taken")
    adherence = round(100 * taken / len(hist)) if hist else None
    today_logs = {l["med_id"]: l["status"] for l in hist if l["date"] == date and l["slot"] == slot}
    rule_ids = [r["id"] for r in rmap.values()]
    audit(sid, pid, "FIND", "query_caregiver_records",
          f"Read {len(hist)} dose logs (7 days, adherence {adherence if adherence is not None else '—'}%) and {len(rmap)} care rules; "
          f"consecutive missed slots before this one: {consecutive}",
          output={"adherence_7d_pct": adherence, "consecutive_missed_slots": consecutive, "already_logged_today": today_logs,
                  "rules": {k: v["value"] for k, v in rmap.items()}},
          source_ids=[l["id"] for l in hist[:12]] + rule_ids,
          source_urls=[caregiver.url("logs", pid)] + [caregiver.url("rules", rec_id=i) for i in rule_ids])
    if due and all(today_logs.get(m["id"]) == "taken" for m in due):
        audit(sid, pid, "PLAN", "plan_checkin", "All due doses already confirmed today → no call needed")
        _save(sid, status="skipped", phase="ended", outcome="already_confirmed", ended_at=db.now_iso())
        return get_session(sid)

    max_retries = int(rmap.get("max_retries", {}).get("value", config.MAX_RETRIES))
    retry_s = int(rmap.get("retry_interval_seconds", {}).get("value", config.RETRY_INTERVAL_S))
    _save(sid, due_med_ids=[m["id"] for m in due], max_attempts=1 + max_retries,
          flags={"consecutive_missed_before": consecutive, "retry_interval_s": retry_s})
    if ring:
        ring_now(sid)
    return get_session(sid)


def ring_now(sid: str) -> dict:
    with _lock(sid):
        s = get_session(sid)
        attempts = s["attempts"] + 1
        deadline = (db.now_local() + timedelta(seconds=config.RING_TIMEOUT_S)).isoformat(timespec="seconds")
        _save(sid, status="ringing", attempts=attempts, ring_deadline=deadline, next_ring_at=None)
        p = caregiver.get_patient(s["patient_id"])
        audit(sid, s["patient_id"], "ACT", "place_call",
              f"Calling {p['name']} at {p['phone']} — attempt {attempts} of {s['max_attempts']}",
              output={"attempt": attempts, "ring_timeout_s": config.RING_TIMEOUT_S},
              source_ids=[s["patient_id"]], source_urls=[caregiver.url("patient", s["patient_id"])])
        return get_session(sid)


def handle_no_answer(sid: str, reason: str = "no_answer") -> dict:
    with _lock(sid):
        s = get_session(sid)
        if not s or s["status"] != "ringing":
            return s
        pid = s["patient_id"]
        audit(sid, pid, "ACT", "call_unanswered", f"Attempt {s['attempts']} not answered ({reason})",
              output={"attempt": s["attempts"], "reason": reason})
        rmap = caregiver.rules_map()
        if s["attempts"] < s["max_attempts"]:
            wait = int(_flags(s).get("retry_interval_s", config.RETRY_INTERVAL_S))
            nxt = (db.now_local() + timedelta(seconds=wait)).isoformat(timespec="seconds")
            _save(sid, status="retry_wait", next_ring_at=nxt, ring_deadline=None)
            rids, rurls = _rule_src(rmap, "max_retries", "retry_interval_seconds")
            audit(sid, pid, "PLAN", "schedule_retry",
                  f"Retry {s['attempts']} of {s['max_attempts'] - 1} scheduled in {wait}s",
                  output={"next_ring_at": nxt}, source_ids=rids, source_urls=rurls)
            return get_session(sid)
        return _finalize_unreachable(s, rmap)


def _finalize_unreachable(s: dict, rmap: dict) -> dict:
    sid, pid = s["id"], s["patient_id"]
    ctx = _ctx(s)
    retries = s["max_attempts"] - 1
    rids, rurls = _rule_src(rmap, "max_retries")
    audit(sid, pid, "PLAN", "escalate_unreachable",
          f"No answer after {retries} retries → mark doses unconfirmed and notify family immediately",
          source_ids=rids, source_urls=rurls)
    _save(sid, status="unreachable", phase="ended", outcome="unreachable", ended_at=db.now_iso(), ring_deadline=None)
    s = get_session(sid)
    for m in ctx["due"]:
        _log_dose(s, m["id"], "uncertain", f"No answer after {s['attempts']} call attempts", None)
    _alert(s, "unreachable", "high",
           f"[Saathi] {ctx['patient']['name']} did not answer the {s['slot']} medicine check-in call after "
           f"{s['attempts']} attempts ({s['date']}). Doses are unconfirmed — please check on them.",
           {"attempts": s["attempts"]})
    verifier.verify_session_complete(get_session(sid))
    _save(sid, summary=f"{s['slot'].title()} check-in: no answer after {s['attempts']} attempts. "
                       f"Doses marked unconfirmed; family notified.")
    audit(sid, pid, "SYSTEM", "session_closed", "Session closed: unreachable")
    return get_session(sid)


def answer(sid: str, lang: str | None = None) -> dict:
    with _lock(sid):
        s = get_session(sid)
        if not s:
            raise AgentError("Unknown call")
        if s["status"] != "ringing":
            raise AgentError(f"Call is not ringing (status: {s['status']})")
        lang = lang if lang in ("en", "hi") else s["language"]
        _save(sid, status="in_progress", answered_at=db.now_iso(), language=lang, phase="dose", ring_deadline=None)
        s = get_session(sid)
        ctx = _ctx(s)
        audit(sid, s["patient_id"], "ACT", "call_answered", f"Call answered on attempt {s['attempts']} (language: {lang})")
        text, en = R.greeting(lang, ctx["name"], s["slot"], ctx["due"], ctx["family"])
        turn = _speak(s, text, en, lang)
        return {"session": get_session(sid), "agent": {"text": text, "text_en": en, "lang": lang, "turn_id": turn["id"]},
                "ended": False}


def reply(sid: str, text: str, lang_hint: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise AgentError("Empty reply")
    with _lock(sid):
        s = get_session(sid)
        if not s:
            raise AgentError("Unknown call")
        if s["status"] != "in_progress":
            raise AgentError(f"Call is not active (status: {s['status']})")
        return _process_reply(s, text[:1000], lang_hint)


def _process_reply(s: dict, text: str, lang_hint: str | None) -> dict:
    sid, pid = s["id"], s["patient_id"]
    ctx = _ctx(s)
    f = _flags(s)
    rmap = ctx["rules"]
    phase = s["phase"]
    pending = [m for m in ctx["due"] if m["id"] not in f["med_status"]]
    last_q = next((t["translation_en"] for t in reversed(_turns(sid)) if t["speaker"] == "agent"), "")

    # ---- STEP 02 PLAN: understand the reply
    cls = classifier.understand(text, pending if phase == "dose" else ctx["due"], phase, last_q)
    lang = cls["language"] if cls["language"] in ("en", "hi") else (lang_hint or s["language"])
    pturn = _add_turn(sid, "patient", text, lang, translation_en=cls.get("english_translation"),
                      corrected=cls.get("corrected_text"), classification=cls)
    audit(sid, pid, "PLAN", "classify_response",
          f"Understood ({cls['source']}): dose={cls['dose_status']}"
          + (f", symptoms={cls['symptoms']}" if cls["symptoms"] else "")
          + (", EMERGENCY" if cls["emergency"] else "") + (", advice request" if cls["advice_request"] else "")
          + (", double dose" if cls["double_dose"] else "")
          + (", schedule change" if cls["schedule_change"]["detected"] else ""),
          input={"text": text, "phase": phase, "pending_meds": [m["id"] for m in pending]},
          output={k: cls.get(k) for k in ("language", "dose_status", "per_med", "symptoms", "emergency", "emergency_signs",
                                          "advice_request", "double_dose", "schedule_change", "wellbeing", "confidence",
                                          "source", "llm_used", "llm_note", "rules_view", "llm_view") if k in cls},
          source_ids=[pturn["id"]], source_urls=[caregiver.url("session", rec_id=sid)])

    say: list[tuple[str, str]] = []
    end = False
    outcome = None
    quote = f"“{cls.get('english_translation') or text}”"
    pname = ctx["patient"]["name"]

    # ---- Emergency overrides everything
    if cls["emergency"]:
        rids, rurls = _rule_src(rmap, "emergency_policy")
        audit(sid, pid, "FIND", "lookup_rule", "Emergency policy: advise 112 and notify every family contact",
              source_ids=rids, source_urls=rurls)
        _alert(s, "emergency", "critical",
               f"[Saathi] URGENT: {pname} reported possible emergency signs ({', '.join(cls['emergency_signs'])}) "
               f"during the {s['slot']} check-in and was told to call 112. Please contact them now. Said: {quote}",
               {"signs": cls["emergency_signs"], "text": text}, pturn["id"])
        f["concern_raised"] = True
        say.append(R.emergency(lang, ctx["family_all"]))
        end, outcome = True, "emergency"

    # ---- Dose status (dose phase only)
    resolved_now_taken, resolved_now_missed = [], []
    if not end and phase == "dose" and pending:
        per = cls["per_med"]
        if cls["double_dose"]:
            for m in pending:
                if per.get(m["id"]) in ("taken", "unclear", None):
                    per[m["id"]] = "double"
        for m in pending:
            st = per.get(m["id"], "unclear")
            if st == "taken":
                f["med_status"][m["id"]] = "taken"
                _log_dose(s, m["id"], "taken", text, pturn["id"])
                resolved_now_taken.append(m)
            elif st == "double":
                f["med_status"][m["id"]] = "double_dose_reported"
                _log_dose(s, m["id"], "double_dose_reported", text, pturn["id"])
            elif st == "not_taken":
                f["med_status"][m["id"]] = "missed"
                _log_dose(s, m["id"], "missed", text, pturn["id"])
                resolved_now_missed.append(m)
        if resolved_now_missed:
            consec = int(f.get("consecutive_missed_before", 0))
            threshold = int(rmap.get("consecutive_miss_escalation", {}).get("value", 2))
            severity = "high" if consec + 1 >= threshold else "medium"
            rids, rurls = _rule_src(rmap, "missed_dose_notify", "consecutive_miss_escalation")
            audit(sid, pid, "FIND", "lookup_rule",
                  f"Missed dose → notify family; {consec} consecutive missed slot(s) before this one → severity {severity}",
                  source_ids=rids, source_urls=rurls)
            _alert(s, "missed_dose", severity,
                   f"[Saathi] {pname} has NOT taken the {s['slot']} dose of {R.med_names(resolved_now_missed, 'en')} "
                   f"({s['date']}). Said: {quote}",
                   {"med_ids": [m["id"] for m in resolved_now_missed], "consecutive_before": consec}, pturn["id"])
        if resolved_now_taken and resolved_now_missed:
            say.append(R.ack_partial(lang, resolved_now_taken, resolved_now_missed))
            say.append(R.missed(lang, resolved_now_missed, ctx["doctor"], ctx["family"], include_ack=False,
                                include_tail=not cls["advice_request"]))
        elif resolved_now_missed:
            say.append(R.missed(lang, resolved_now_missed, ctx["doctor"], ctx["family"],
                                include_tail=not cls["advice_request"]))
        elif resolved_now_taken:
            say.append(R.ack_taken(lang, resolved_now_taken))

    # ---- Double dose reported
    if not end and cls["double_dose"]:
        rids, rurls = _rule_src(rmap, "medical_advice_policy")
        _alert(s, "double_dose", "high",
               f"[Saathi] {pname} said they may have taken an extra/double dose during the {s['slot']} check-in. "
               f"They were asked to contact {ctx['doctor']}. Said: {quote}", {"text": text}, pturn["id"])
        f["concern_raised"] = True
        say.append(R.double_dose_care(lang, ctx["doctor"], ctx["family"]))

    # ---- Symptoms
    new_sym = [x for x in cls["symptoms"] if x not in f["alerted_symptoms"]]
    if not end and new_sym:
        rids, rurls = _rule_src(rmap, "symptom_escalation")
        audit(sid, pid, "FIND", "lookup_rule", f"Symptom policy: advise doctor ({ctx['doctor']}) and notify family",
              source_ids=rids + [pid], source_urls=rurls + [caregiver.url("patient", pid)])
        _alert(s, "symptom", "high",
               f"[Saathi] {pname} reported {', '.join(new_sym)} during the {s['slot']} check-in. "
               f"They were advised to rest and speak to {ctx['doctor']}. Said: {quote}",
               {"symptoms": new_sym, "text": text}, pturn["id"])
        f["alerted_symptoms"] += new_sym
        f["concern_raised"] = True
        say.append(R.symptom_care(lang, new_sym, ctx["doctor"], ctx["family"]))
        if not f["doctor_share_requested"]:
            _request_approval(s, "share_report_with_doctor",
                              {"doctor": ctx["doctor"], "doctor_contact": ctx["patient"]["doctor_contact"],
                               "report": f"{pname} reported {', '.join(new_sym)} during the {s['slot']} medicine check-in on "
                                         f"{s['date']}. Said: {quote}"},
                              "Sharing health information outside the family needs caregiver approval.", rmap)
            f["doctor_share_requested"] = True

    # ---- Medical advice request (input guardrail)
    if not end and cls["advice_request"]:
        rids, rurls = _rule_src(rmap, "medical_advice_policy")
        audit(sid, pid, "GUARDRAIL", "medical_advice_blocked",
              "Elder asked for medical/dosing advice → no advice given, referred to doctor",
              input={"text": text}, output={"advice_given": False, "referred_to": ctx["doctor"]},
              source_ids=rids + [pturn["id"]], source_urls=rurls)
        _alert(s, "advice_request", "medium",
               f"[Saathi] {pname} asked: {quote} during the {s['slot']} check-in. No advice was given; "
               f"they were told to ask {ctx['doctor']} first.", {"text": text}, pturn["id"])
        say.append(R.advice_refusal(lang, ctx["doctor"], ctx["family"]))

    # ---- Prescription change reported (irreversible → approval)
    sc = cls["schedule_change"]
    if not end and sc["detected"]:
        med_id = sc.get("med_id")
        stop = bool(re.search(r"stop|discontinu|remov|cancel|band|बंद|hata|हटा|chhod|छोड़", text.lower()))
        med = next((m for m in ctx["due"] if m["id"] == med_id), None) or \
            next((m for m in caregiver.schedule(pid) if m["id"] == med_id), None)
        _request_approval(s, "update_medication_schedule",
                          {"med_id": med_id, "med_name": med["name"] if med else None,
                           "proposed_change": "deactivate" if (stop and med_id) else "review",
                           "elder_statement": text},
                          "Changing the medicine schedule cannot be undone by the agent; caregiver must confirm with the doctor.",
                          rmap)
        _alert(s, "schedule_change", "medium",
               f"[Saathi] {pname} says the doctor changed a medicine: {quote}. Review the pending approval on the dashboard.",
               {"text": text, "med_id": med_id}, pturn["id"])
        say.append(R.schedule_change_noted(lang, ctx["family"]))

    # ---- Decide next step
    if not end:
        still_pending = [m for m in ctx["due"] if m["id"] not in f["med_status"]]
        if phase == "dose":
            if still_pending:
                progressed = bool(resolved_now_taken or resolved_now_missed)
                other_topic = cls["advice_request"] or bool(new_sym) or sc["detected"] or cls["double_dose"]
                # Only a reply that neither answered anything nor raised another topic counts as "unclear".
                unclear = s["unclear_count"] + (0 if progressed or other_topic else 1)
                _save(sid, unclear_count=unclear)
                if unclear >= 2:
                    for m in still_pending:
                        f["med_status"][m["id"]] = "uncertain"
                        _log_dose(s, m["id"], "uncertain", f"Not confirmed after {unclear} attempts. Last reply: {text}", pturn["id"])
                    audit(sid, pid, "PLAN", "insufficient_evidence",
                          "Evidence still insufficient after re-asking → returning UNCERTAIN instead of guessing",
                          output={"med_ids": [m["id"] for m in still_pending]})
                    _alert(s, "uncertain", "low",
                           f"[Saathi] {pname}'s {s['slot']} dose of {R.med_names(still_pending, 'en')} could not be confirmed "
                           f"on the call. Please check with them.", {"med_ids": [m["id"] for m in still_pending]}, pturn["id"])
                    say.append(R.uncertain_logged(lang, ctx["family"]))
                    phase = "wellness"
                elif resolved_now_taken or resolved_now_missed:
                    say.append(R.ask_specific(lang, still_pending))
                elif cls["advice_request"] or new_sym or sc["detected"] or cls["double_dose"]:
                    say.append(R.reask_after_other(lang, s["slot"], still_pending))
                else:
                    say.append(R.reask(lang, s["slot"], still_pending))
            else:
                phase = "wellness"
            if phase == "wellness":
                if f["concern_raised"]:
                    end, outcome = True, "completed_with_concerns"
                else:
                    say.append(R.wellness_q(lang))
                    audit(sid, pid, "PLAN", "next_step", "All doses resolved → ask a short wellbeing question")
        else:  # wellness phase: answer processed above → close the call
            if cls["wellbeing"] == "good" and not new_sym:
                say.append(R.wellness_good(lang))
            end, outcome = True, ("completed_with_concerns" if f["concern_raised"] else "completed")

    turn_count = s["turn_count"] + 1
    if not end and turn_count >= config.MAX_TURNS:
        end, outcome = True, "max_turns"
    if end and outcome != "emergency":
        say.append(R.closing(lang, ctx["name"]))

    _save(sid, flags=f, phase="ended" if end else phase, language=lang, turn_count=turn_count)
    s = get_session(sid)
    text_l = " ".join(x[0] for x in say)
    text_en = " ".join(x[1] for x in say)
    turn = _speak(s, text_l, text_en, lang)
    if end:
        _finalize(get_session(sid), outcome or "completed")
    return {"session": get_session(sid), "agent": {"text": turn["text"], "text_en": turn["translation_en"], "lang": lang,
                                                    "turn_id": turn["id"]},
            "ended": end, "classification": {k: cls.get(k) for k in ("dose_status", "symptoms", "emergency", "advice_request",
                                                                     "double_dose", "source")}}


def _finalize(s: dict, outcome: str) -> None:
    sid, pid = s["id"], s["patient_id"]
    f = _flags(s)
    ctx = _ctx(s)
    for m in ctx["due"]:
        if m["id"] not in f["med_status"]:
            f["med_status"][m["id"]] = "uncertain"
            _log_dose(s, m["id"], "uncertain", f"Call ended ({outcome}) before this dose was confirmed", None)
    _save(sid, status="completed", phase="ended", outcome=outcome, ended_at=db.now_iso(), flags=f)
    s = get_session(sid)
    verifier.verify_session_complete(s)
    facts = {"slot": s["slot"], "date": s["date"], "dose_status": f["med_status"], "symptoms": f["alerted_symptoms"],
             "outcome": outcome}
    summary = llm.summarize(_turns(sid), facts) if config.llm_enabled() else None
    if not summary:
        names = {m["id"]: m["name"] for m in ctx["due"]}
        groups: dict[str, list[str]] = {}
        for mid, st in f["med_status"].items():
            groups.setdefault(st, []).append(names.get(mid, mid))
        parts = [f"{st.replace('_', ' ')}: {', '.join(v)}" for st, v in groups.items()]
        summary = f"{s['slot'].title()} check-in {outcome.replace('_', ' ')}. " + "; ".join(parts) + "."
        if f["alerted_symptoms"]:
            summary += f" Symptoms reported: {', '.join(f['alerted_symptoms'])}."
    _save(sid, summary=summary.strip())
    audit(sid, pid, "SYSTEM", "session_closed", f"Session closed: {outcome}", output={"summary": summary.strip()})


def hangup(sid: str, by: str = "elder") -> dict:
    with _lock(sid):
        s = get_session(sid)
        if not s:
            raise AgentError("Unknown call")
        if s["status"] == "ringing":
            return handle_no_answer(sid, "declined")
        if s["status"] != "in_progress":
            return s
        _add_turn(sid, "system", f"Call ended by {by} before the check-in finished.", "en",
                  translation_en=f"Call ended by {by} before the check-in finished.")
        f = _flags(s)
        ctx = _ctx(s)
        pending = [m for m in ctx["due"] if m["id"] not in f["med_status"]]
        if pending:
            _alert(s, "uncertain", "low",
                   f"[Saathi] The {s['slot']} check-in with {ctx['patient']['name']} ended early; "
                   f"{R.med_names(pending, 'en')} not confirmed.", {"med_ids": [m["id"] for m in pending]})
        _finalize(s, "ended_early" if by == "elder" else "timed_out")
        return get_session(sid)


# ----------------------------------------------------------------------------- approvals

def decide_approval(ap_id: str, decision: str, by: str = "caregiver", note: str = "") -> dict:
    ap = db.q1("SELECT * FROM approvals WHERE id = ?", (ap_id,))
    if not ap:
        raise AgentError("Unknown approval")
    if ap["status"] != "pending":
        raise AgentError(f"Approval already {ap['status']}")
    sid, pid = ap["session_id"], ap["patient_id"]
    if decision not in ("approve", "reject"):
        raise AgentError("decision must be approve or reject")
    db.update("approvals", "id", ap_id, {"status": "approved" if decision == "approve" else "rejected",
                                         "decided_at": db.now_iso(), "decided_by": by, "decision_note": note})
    audit(sid, pid, "ACT", "human_approval_decision", f"{by} {decision}d {ap['action_type']} ({ap_id})",
          input={"note": note}, source_ids=[ap_id], source_urls=[caregiver.url("approval", rec_id=ap_id)])
    if decision == "reject":
        return db.q1("SELECT * FROM approvals WHERE id = ?", (ap_id,))

    payload = ap["payload"] or {}
    result: dict = {}
    ok = True
    if ap["action_type"] == "update_medication_schedule":
        mid = payload.get("med_id")
        if payload.get("proposed_change") == "deactivate" and mid:
            db.update("medications", "id", mid, {"active": 0})
            result = {"med_id": mid, "active": 0}
            audit(sid, pid, "ACT", "update_medication_schedule", f"Deactivated {mid} after approval",
                  output=result, source_ids=[mid, ap_id], source_urls=[caregiver.url("schedule", pid, mid)])
            ok = verifier.verify_med_active(sid, pid, mid, 0)
        else:
            result = {"note": "Caregiver confirmed; schedule to be edited manually after doctor confirmation."}
            audit(sid, pid, "ACT", "update_medication_schedule", "Approved as review-only (no automatic change)",
                  output=result, source_ids=[ap_id])
    elif ap["action_type"] == "share_report_with_doctor":
        nt = notifier.send_doctor_report(pid, sid, payload.get("report", ""))
        result = {"notification_id": nt["id"], "recipient": nt["recipient"]}
        audit(sid, pid, "ACT", "share_report_with_doctor", f"Report sent to {nt['recipient']} (mock email)",
              output=result, source_ids=[nt["id"], ap_id], source_urls=[caregiver.url("notification", rec_id=nt["id"])])
        row = db.q1("SELECT status FROM notifications WHERE id = ?", (nt["id"],))
        ok = bool(row and row["status"] == "sent")
        audit(sid, pid, "VERIFY", "verify_doctor_report", f"{'PASS' if ok else 'FAIL'} — verify_doctor_report",
              input={"expected": {"status": "sent"}}, output={"actual": row, "passed": ok}, source_ids=[nt["id"]])
    db.update("approvals", "id", ap_id, {"status": "executed" if ok else "failed", "result": result})
    return db.q1("SELECT * FROM approvals WHERE id = ?", (ap_id,))
