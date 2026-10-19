"""Family notifications.

Channels:
- dashboard  : every alert appears on the caregiver dashboard (always).
- sms_mock   : a simulated SMS written to the outbox (no real messages are sent to mock numbers).
- telegram   : REAL message via a Telegram bot, only when TELEGRAM_BOT_TOKEN is set and the
               contact has a telegram_chat_id — handy for showing a live phone notification in the demo.
- email_mock : simulated email (used for the approval-gated doctor report).

Routing by severity: critical/high → all family contacts, medium → primary contact,
low → dashboard only.
"""
import logging

import httpx

from .. import caregiver, config, db
from .trace import audit

log = logging.getLogger("saathi.notify")


def _targets(pid: str, severity: str) -> list[dict]:
    cs = caregiver.contacts(pid)
    if severity in ("critical", "high"):
        return cs
    if severity == "medium":
        return cs[:1]
    return []


def _send_telegram(chat_id: str, text: str) -> tuple[str, str | None]:
    try:
        r = httpx.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
                       json={"chat_id": chat_id, "text": text}, timeout=8)
        ok = r.status_code == 200 and r.json().get("ok")
        return ("sent" if ok else "failed"), (str(r.json().get("result", {}).get("message_id")) if ok else r.text[:200])
    except Exception as e:
        return "failed", str(e)[:200]


def notify_family(alert: dict, session_id: str | None) -> list[dict]:
    pid = alert["patient_id"]
    sent = []
    for c in _targets(pid, alert["severity"]):
        channels = [("sms_mock", c["phone"])]
        if config.TELEGRAM_BOT_TOKEN and c.get("telegram_chat_id"):
            channels.append(("telegram", c["telegram_chat_id"]))
        for channel, recipient in channels:
            if channel == "telegram":
                status, ref = _send_telegram(recipient, alert["message"])
            else:
                status, ref = "sent", "mock-outbox"
            row = {
                "id": db.new_id("NT"), "alert_id": alert["id"], "patient_id": pid, "contact_id": c["id"],
                "channel": channel, "recipient": f"{c['name']} <{recipient}>", "message": alert["message"],
                "status": status, "provider_ref": ref, "created_at": db.now_iso(),
            }
            db.insert("notifications", row)
            sent.append(row)
    audit(session_id, pid, "ACT", "notify_family",
          f"Notified {len({n['contact_id'] for n in sent})} family contact(s) about {alert['type']} ({alert['severity']})"
          if sent else f"Alert {alert['id']} shown on dashboard only (severity {alert['severity']})",
          input={"alert_id": alert["id"], "severity": alert["severity"]},
          output={"notifications": [{k: n[k] for k in ("id", "channel", "recipient", "status")} for n in sent]},
          source_ids=[alert["id"]] + [n["contact_id"] for n in sent] + [n["id"] for n in sent],
          source_urls=[caregiver.url("alert", rec_id=alert["id"])] +
                      [caregiver.url("contacts", pid, n["contact_id"]) for n in sent] +
                      [caregiver.url("notification", rec_id=n["id"]) for n in sent])
    return sent


def send_doctor_report(pid: str, session_id: str | None, message: str) -> dict:
    p = caregiver.get_patient(pid)
    row = {
        "id": db.new_id("NT"), "alert_id": None, "patient_id": pid, "contact_id": "DOCTOR",
        "channel": "email_mock", "recipient": f"{p['doctor_name']} <{p['doctor_contact']}>", "message": message,
        "status": "sent", "provider_ref": "mock-outbox", "created_at": db.now_iso(),
    }
    db.insert("notifications", row)
    return row
