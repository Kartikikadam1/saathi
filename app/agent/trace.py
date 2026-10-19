"""Audit trail. Every agent step writes one row: stage, tool, input, output and the source
records (IDs + URLs) the step relied on. The dashboard's Trace view renders these rows."""
from .. import db

STAGES = ("COLLECT", "PLAN", "FIND", "ACT", "VERIFY", "GUARDRAIL", "SYSTEM")


def audit(session_id: str | None, patient_id: str | None, stage: str, tool: str, summary: str,
          input=None, output=None, source_ids=(), source_urls=()) -> None:
    assert stage in STAGES, stage
    db.insert("audit_log", {
        "session_id": session_id, "patient_id": patient_id, "stage": stage, "tool": tool, "summary": summary,
        "input": input if input is not None else {}, "output": output if output is not None else {},
        "source_ids": list(dict.fromkeys(source_ids)), "source_urls": list(dict.fromkeys(source_urls)),
        "created_at": db.now_iso(),
    })


def trace(session_id: str) -> list[dict]:
    return db.q("SELECT * FROM audit_log WHERE session_id = ? ORDER BY id", (session_id,))
