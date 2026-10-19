"""SQLite data store: schema, connection handling and small query helpers.

Every record has a stable, human-readable ID (P-001, MED-001, DL-1a2b3c ...) so the
agent can cite exactly which record a decision was based on.
"""
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

from . import config

_write_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    age INTEGER,
    language TEXT DEFAULT 'en',
    phone TEXT,
    doctor_name TEXT,
    doctor_contact TEXT,
    auto_calls INTEGER DEFAULT 0,
    is_test INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS medications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    name TEXT NOT NULL,
    dose TEXT,
    slot TEXT NOT NULL,             -- morning | evening
    time TEXT,                      -- HH:MM local
    instructions TEXT,
    aliases TEXT DEFAULT '[]',      -- JSON list of spoken names
    active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS family_contacts (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    name TEXT NOT NULL,
    relation TEXT,
    phone TEXT,
    email TEXT,
    telegram_chat_id TEXT,
    priority INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS consents (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL UNIQUE,
    call_consent INTEGER DEFAULT 0,
    recording_consent INTEGER DEFAULT 0,
    family_sharing_consent INTEGER DEFAULT 0,
    granted_by TEXT,
    granted_at TEXT,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS care_rules (
    id TEXT PRIMARY KEY,
    key TEXT UNIQUE,
    value TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS call_sessions (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL,           -- ringing|retry_wait|in_progress|completed|unreachable|declined_all|blocked_no_consent|skipped
    phase TEXT DEFAULT 'dose',      -- dose|wellness|ended
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    ring_deadline TEXT,
    next_ring_at TEXT,
    language TEXT DEFAULT 'en',
    due_med_ids TEXT DEFAULT '[]',
    flags TEXT DEFAULT '{}',
    unclear_count INTEGER DEFAULT 0,
    turn_count INTEGER DEFAULT 0,
    outcome TEXT,
    summary TEXT,
    trigger TEXT,                   -- manual|scheduler|test
    is_test INTEGER DEFAULT 0,
    created_at TEXT,
    answered_at TEXT,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS call_turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    speaker TEXT NOT NULL,          -- agent|patient|system
    text TEXT NOT NULL,
    lang TEXT,
    translation_en TEXT,
    corrected_text TEXT,
    classification TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS dose_logs (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    med_id TEXT NOT NULL,
    date TEXT NOT NULL,
    slot TEXT NOT NULL,
    status TEXT NOT NULL,           -- taken|missed|uncertain|double_dose_reported
    source_session_id TEXT,
    source_turn_id TEXT,
    evidence_text TEXT,
    verified INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(patient_id, med_id, date, slot)
);
CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    session_id TEXT,
    type TEXT NOT NULL,
    severity TEXT NOT NULL,         -- critical|high|medium|low
    message TEXT NOT NULL,
    evidence TEXT DEFAULT '{}',
    status TEXT DEFAULT 'open',
    created_at TEXT,
    acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id TEXT PRIMARY KEY,
    alert_id TEXT,
    patient_id TEXT,
    contact_id TEXT,
    channel TEXT,
    recipient TEXT,
    message TEXT,
    status TEXT,
    provider_ref TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    session_id TEXT,
    action_type TEXT NOT NULL,
    payload TEXT DEFAULT '{}',
    reason TEXT,
    status TEXT DEFAULT 'pending',  -- pending|approved|rejected|executed|failed
    requested_at TEXT,
    decided_at TEXT,
    decided_by TEXT,
    decision_note TEXT,
    result TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT,
    patient_id TEXT,
    stage TEXT NOT NULL,            -- COLLECT|PLAN|FIND|ACT|VERIFY|GUARDRAIL|SYSTEM
    tool TEXT,
    summary TEXT,
    input TEXT,
    output TEXT,
    source_ids TEXT DEFAULT '[]',
    source_urls TEXT DEFAULT '[]',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS test_runs (
    id TEXT PRIMARY KEY,
    batch_id TEXT,
    tc_id TEXT,
    name TEXT,
    input TEXT,
    expected TEXT,
    actual TEXT,
    checks TEXT,
    passed INTEGER,
    session_id TEXT,
    patient_id TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sessions_patient ON call_sessions(patient_id, date, slot);
CREATE INDEX IF NOT EXISTS ix_turns_session ON call_turns(session_id, seq);
CREATE INDEX IF NOT EXISTS ix_audit_session ON audit_log(session_id, id);
CREATE INDEX IF NOT EXISTS ix_doses_patient ON dose_logs(patient_id, date);
"""


def now_local() -> datetime:
    return datetime.now(config.TZ)


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def today() -> str:
    return now_local().date().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    """Serialised write transaction."""
    with _write_lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)


JSON_FIELDS = {"aliases", "due_med_ids", "flags", "classification", "evidence", "payload", "result",
               "input", "output", "source_ids", "source_urls", "expected", "actual", "checks"}


def row_to_dict(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for k in list(d.keys()):
        if k in JSON_FIELDS and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except (ValueError, TypeError):
                pass
    return d


def q(sql: str, params=()) -> list[dict]:
    conn = _connect()
    try:
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def q1(sql: str, params=()) -> dict | None:
    rows = q(sql, params)
    return rows[0] if rows else None


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def insert(table: str, data: dict, conn=None) -> dict:
    data = {k: (dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}
    cols = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    sql = f"INSERT INTO {table} ({cols}) VALUES ({marks})"
    if conn is not None:
        conn.execute(sql, tuple(data.values()))
    else:
        with tx() as c:
            c.execute(sql, tuple(data.values()))
    return data


def update(table: str, key: str, key_val, data: dict, conn=None) -> None:
    data = {k: (dumps(v) if isinstance(v, (dict, list)) else v) for k, v in data.items()}
    sets = ", ".join(f"{k} = ?" for k in data)
    sql = f"UPDATE {table} SET {sets} WHERE {key} = ?"
    params = tuple(data.values()) + (key_val,)
    if conn is not None:
        conn.execute(sql, params)
    else:
        with tx() as c:
            c.execute(sql, params)
