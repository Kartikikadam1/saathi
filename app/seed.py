"""Synthetic demo data. No real patient information is used anywhere in this project."""
from datetime import timedelta

from . import config, db

DEMO_PATIENT_ID = "P-001"

RULES = [
    ("RULE-001", "max_retries", str(config.MAX_RETRIES), "Retries after the first unanswered call attempt before marking the patient unreachable."),
    ("RULE-002", "retry_interval_seconds", str(config.RETRY_INTERVAL_S), "Wait between call attempts (demo: seconds; production: ~10 minutes)."),
    ("RULE-003", "missed_dose_notify", "primary", "A missed dose notifies the primary family contact immediately."),
    ("RULE-004", "symptom_escalation", "advise_doctor+notify_primary", "Reported symptoms: advise contacting the doctor and notify the primary contact."),
    ("RULE-005", "medical_advice_policy", "never_advise_refer_doctor", "The agent never gives dosing or medical advice; it refers the patient to their doctor."),
    ("RULE-006", "emergency_policy", "advise_112+notify_all", "Emergency signs: tell the patient to call 112 and notify every family contact."),
    ("RULE-007", "approval_required_actions", "update_medication_schedule,share_report_with_doctor", "Actions that cannot be undone need caregiver approval first."),
    ("RULE-008", "consecutive_miss_escalation", "2", "Consecutive missed slots that raise a missed-dose alert to high severity."),
]

MEDS = [
    ("MED-001", "Metformin", "500 mg, 1 tablet", "morning", "09:00", "After breakfast",
     ["metformin", "sugar", "sugar tablet", "sugar ki goli", "diabetes", "शुगर", "मेटफॉर्मिन", "मेटफार्मिन"]),
    ("MED-002", "Amlodipine", "5 mg, 1 tablet", "morning", "09:00", "Blood pressure",
     ["amlodipine", "bp", "b.p.", "blood pressure", "bp ki goli", "बीपी", "ब्लड प्रेशर", "एम्लोडिपिन"]),
    ("MED-003", "Atorvastatin", "10 mg, 1 tablet", "evening", "21:00", "At bedtime",
     ["atorvastatin", "cholesterol", "कोलेस्ट्रॉल", "एटोरवास्टेटिन"]),
    ("MED-004", "Metformin", "500 mg, 1 tablet", "evening", "20:00", "After dinner",
     ["metformin", "sugar", "sugar tablet", "sugar ki goli", "diabetes", "शुगर", "मेटफॉर्मिन", "मेटफार्मिन"]),
]


def _create_patient(conn, pid: str, name: str, is_test: bool, with_history: bool, consent: bool = True,
                    suffix: str = "") -> None:
    ts = db.now_iso()
    db.insert("patients", {
        "id": pid, "name": name, "age": 74, "language": "en", "phone": "+91-90000-00001 (mock)",
        "doctor_name": "Dr. Anil Mehta", "doctor_contact": "clinic@example.com (mock)",
        "auto_calls": 0, "is_test": 1 if is_test else 0, "created_at": ts,
    }, conn)
    for mid, mname, dose, slot, time, instr, aliases in MEDS:
        db.insert("medications", {
            "id": f"{mid}{suffix}", "patient_id": pid, "name": mname, "dose": dose, "slot": slot,
            "time": time, "instructions": instr, "aliases": aliases, "active": 1,
        }, conn)
    db.insert("family_contacts", {
        "id": f"FAM-001{suffix}", "patient_id": pid, "name": "Rahul Sharma", "relation": "Son",
        "phone": "+91-90000-11111 (mock)", "email": "rahul@example.com", "telegram_chat_id": None, "priority": 1,
    }, conn)
    db.insert("family_contacts", {
        "id": f"FAM-002{suffix}", "patient_id": pid, "name": "Priya Sharma", "relation": "Daughter",
        "phone": "+91-90000-22222 (mock)", "email": "priya@example.com", "telegram_chat_id": None, "priority": 2,
    }, conn)
    db.insert("consents", {
        "id": f"CON-001{suffix}", "patient_id": pid,
        "call_consent": 1 if consent else 0, "recording_consent": 1 if consent else 0,
        "family_sharing_consent": 1 if consent else 0,
        "granted_by": f"{name} (verbal), witnessed by Rahul Sharma" if consent else None,
        "granted_at": ts if consent else None,
        "notes": "Synthetic consent record for demo." if consent else "Consent not granted / revoked.",
    }, conn)
    if with_history:
        _seed_history(conn, pid, suffix)


def _seed_history(conn, pid: str, suffix: str) -> None:
    """Six days of plausible adherence history so the dashboard has context."""
    base = db.now_local().date()
    pattern = {
        1: {"morning": "taken", "evening": "taken"},
        2: {"morning": "taken", "evening": "missed"},
        3: {"morning": "taken", "evening": "taken"},
        4: {"morning": "uncertain", "evening": "taken"},
        5: {"morning": "taken", "evening": "taken"},
        6: {"morning": "taken", "evening": "taken"},
    }
    for days_ago, slots in pattern.items():
        d = (base - timedelta(days=days_ago)).isoformat()
        for mid, _n, _d, slot, *_ in MEDS:
            status = slots[slot]
            db.insert("dose_logs", {
                "id": db.new_id("DL"), "patient_id": pid, "med_id": f"{mid}{suffix}", "date": d, "slot": slot,
                "status": status, "source_session_id": "SEED-HISTORY", "source_turn_id": None,
                "evidence_text": "Synthetic history record", "verified": 1,
                "created_at": db.now_iso(), "updated_at": db.now_iso(),
            }, conn)


def seed_rules(conn) -> None:
    for rid, key, value, desc in RULES:
        conn.execute("INSERT OR REPLACE INTO care_rules (id, key, value, description) VALUES (?,?,?,?)",
                     (rid, key, value, desc))


def reset_demo() -> None:
    """Wipe everything and recreate the demo patient (used on first boot and by 'Reset demo')."""
    with db.tx() as c:
        for t in ("audit_log", "call_turns", "call_sessions", "dose_logs", "alerts", "notifications",
                  "approvals", "test_runs", "consents", "family_contacts", "medications", "patients", "care_rules"):
            c.execute(f"DELETE FROM {t}")
        seed_rules(c)
        _create_patient(c, DEMO_PATIENT_ID, "Kamla Sharma", is_test=False, with_history=True)


def ensure_seeded() -> None:
    db.init_db()
    if not db.q1("SELECT id FROM patients WHERE id = ?", (DEMO_PATIENT_ID,)):
        reset_demo()


def create_test_patient(tag: str, consent: bool = True) -> str:
    """Isolated synthetic patient per test run, so tests never touch the demo patient's records."""
    suffix = "-" + db.new_id("T").split("-")[1]
    pid = f"P-TEST{suffix}"
    with db.tx() as c:
        if not db.q1("SELECT id FROM care_rules LIMIT 1"):
            seed_rules(c)
        _create_patient(c, pid, f"Test Patient ({tag})", is_test=True, with_history=False,
                        consent=consent, suffix=suffix)
    return pid
