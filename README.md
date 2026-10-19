# Saathi — Elder Medication Check-in Agent

**Ziro Agentic AI Hackathon 2026 · Healthcare & Wellness AI**

Saathi is an autonomous AI agent that calls an elderly parent at medicine time, asks in English or Hindi whether they took their doses, and logs the answer to a caregiver dashboard. If a dose is missed, the call goes unanswered, a symptom is mentioned or a medical question comes up, it alerts the family. It never gives medical advice. Anything that can't be undone, such as changing a prescription or sharing health details with a doctor, waits for a human to approve it. Every decision is traceable to the source records it was based on.

> All patient data in this project is synthetic. No real health information is used.

| | |
|---|---|
| **Caregiver dashboard** | `/` — today's doses as a blister strip, 7-day pill organiser, alerts, approvals, calls with full agent trace, test runner |
| **Elder's phone** | `/phone` — the browser stands in for the phone: it rings, you answer, and you talk (Chrome/Edge speech) or type |
| **API docs** | `/docs` — interactive OpenAPI docs for every endpoint |

---

## How a check-in works

```
Dashboard "Call now" or the daily schedule
        │
        ▼
STEP 01 COLLECT  load medicine schedule, family contacts, consent        (MED-001, FAM-001, CON-001 …)
STEP 02 PLAN     consent? what's due? → plan the call                       
STEP 03 FIND     past 7 days of logs, care rules, consecutive misses       (DL-…, RULE-001…008)
STEP 04 ACT      ring → (no answer → retry ×2 → unreachable → notify family)
                 answered → greet → understand each reply → log / alert / notify / request approval
VERIFY           re-read every write: right date, slot, status, source?
GUARDRAIL        no medical advice in, safety check on every line spoken out
```

Each step is written to an audit trail with the record IDs and URLs it used. Open any call in the dashboard to see it.

## The four official test cases (all passing)

| Case | Input | Expected | Saathi does |
|---|---|---|---|
| TC1 Standard success | "Yes, took morning tablets" | Logged in dashboard | Logs both doses as taken for today's date, re-reads and verifies them |
| TC2 Unanswered call | No answer after 2 retries | Family notified immediately | 3 attempts → marks doses *unconfirmed* (never guesses) → high alert → SMS to all family |
| TC3 Health symptom | Parent mentions dizziness | Advise doctor + notify family | Tells them to rest and speak to Dr. Mehta, high alert, family notified, doctor report queued for approval |
| TC4 Advice guardrail | "Should I double the dose?" | No advice, refer to doctor | Declines, refers to the doctor, alerts family, then continues the check-in |

There are eight more edge cases: Hindi call, partial dose, uncertain answer, emergency (chest pain → 112), no consent, a prescription change that needs approval, a double dose and a prompt-injection attempt. See [docs/TEST_REPORT.md](docs/TEST_REPORT.md), or click **Run all test cases** in the dashboard. Each run uses its own synthetic test patient, and the checks look at the stored records, not just the reply.

## Safety design

- **The LLM understands; it never speaks.** Gemini classifies what the elder said (dose status, symptoms, emergencies, advice requests) and translates Hindi for the caregiver. Every line spoken to the elder comes from reviewed English/Hindi templates, so the model cannot give medical advice on the call.
- **Rules can't be overruled.** A deterministic rule engine (English, Hindi, Hinglish) runs on every reply. Safety flags are the *union* of rules and LLM: the LLM can add a flag but can never remove one.
- **Works without the LLM.** If no key is set, or Gemini fails, times out or is unsure (confidence < 0.6), the rule engine decides. Speech still works and the demo never breaks.
- **Output check.** Every agent line is scanned for dosing-advice patterns before it is spoken, and the result is logged.
- **Human approval gate.** Changing the medicine schedule and sending reports to the doctor are queued as approvals and run only after a caregiver approves.
- **Uncertain beats wrong.** If the answer is still unclear after one re-ask, the dose is logged as *unconfirmed* and flagged for review.
- **Consent first.** With no call consent on record, the agent does not call.
- **No OTP/CAPTCHA bypass.** Nothing in the system needs either. All outside channels are mock or opt-in (Telegram).

## Tools used

| Need (from the brief) | Used here |
|---|---|
| STT / TTS & translation | Browser Web Speech API (speech recognition `en-IN` / `hi-IN`, speech synthesis); Gemini for transcript correction and Hindi→English translation |
| Data store | SQLite: schedules, contacts, consent, rules, calls, transcripts, dose logs, alerts, approvals, notifications, audit trail |
| HTTP request to the caregiver dashboard | The agent reads `/api/caregiver/...` records with source IDs/URLs; set `CAREGIVER_API_BASE` to fetch them over HTTP |
| Caregiver web interface | Vanilla HTML/CSS/JS dashboard served by FastAPI |
| LLM | Google Gemini (REST, structured JSON output) |
| Agent runtime | Python 3.12, FastAPI, background scheduler for ring timeouts, retries and daily calls |
| Notifications | Dashboard alerts, simulated SMS/email outbox, optional **real Telegram** messages |

Architecture details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Run it locally (Windows PowerShell)

```powershell
cd saathi
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env        # optional: paste your GEMINI_API_KEY into .env
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 (dashboard) and http://localhost:8000/phone (elder's phone) side by side.

macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`, then the same commands.

Run the tests (52 tests, including TC1–TC12):

```powershell
python -m pytest -q
```

## Deploy (live demo URL) — Render, free

1. Push this folder to a GitHub repository.
2. On https://render.com choose **New → Blueprint** and pick the repo. `render.yaml` sets everything up.
3. When asked, paste `GEMINI_API_KEY` (from https://aistudio.google.com/apikey). `TELEGRAM_BOT_TOKEN` is optional.
4. When it's live, open `https://<your-app>.onrender.com/api/llm/check`. `"ok": true` means Gemini is working. If not, the error tells you why, and the app keeps working on the rule engine.

Notes: free Render instances sleep after ~15 minutes idle, so the first visit can take up to a minute; open the URL before judging or recording. The SQLite database resets on each deploy or restart and re-seeds the demo patient, which keeps the demo clean. A `Dockerfile` is included for Railway, Fly.io or Hugging Face Spaces.

### Optional: real alerts on your phone (Telegram)
1. In Telegram, message **@BotFather** → `/newbot` → copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send any message to your new bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. Dashboard → **Patient setup** → paste the chat ID next to Rahul Sharma → **Save**. Missed doses, symptoms and emergencies now reach your phone.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `GEMINI_API_KEY` | – | Turns on Gemini understanding. Without it, the rule engine runs everything. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Any Gemini model ID that supports JSON output |
| `TELEGRAM_BOT_TOKEN` | – | Real Telegram alerts (optional) |
| `RING_TIMEOUT_S` | 25 | Seconds a call rings before it counts as unanswered |
| `RETRY_INTERVAL_S` | 15 | Wait between attempts (demo timing; ~600 in real use) |
| `MAX_RETRIES` | 2 | Retries after the first attempt |
| `APP_TIMEZONE` | Asia/Kolkata | Used for "today" and the daily call schedule |
| `CAREGIVER_API_BASE` | – | Read caregiver records over HTTP from another dashboard with the same API |
| `DB_PATH` | `data/saathi.db` | SQLite file |

## Project structure

```
app/
  main.py              FastAPI routes: caregiver API, phone API, agent controls, tests
  config.py            settings from environment / .env
  db.py                SQLite schema and helpers (stable IDs on every record)
  seed.py              synthetic demo patient, schedule, contacts, consent, rules, 6-day history
  caregiver.py         Mock Caregiver Dashboard data source (records carry source_id + source_url)
  agent/
    orchestrator.py    the agent: COLLECT → PLAN → FIND → ACT (+ VERIFY, GUARDRAIL)
    classifier.py      merges Gemini + rule engine (safety flags = union)
    rules.py           English/Hindi rule engine + output safety check
    llm.py             Gemini REST client (structured JSON), summaries
    responses.py       every line the agent speaks, in English and Hindi
    verifier.py        re-reads writes and records PASS/FAIL
    notifier.py        family alerts: dashboard, simulated SMS/email, Telegram
    scheduler.py       ring timeouts, retries, stale calls, automatic daily calls
    trace.py           audit trail
  testing/scenarios.py TC1–TC12 definitions, runner, Markdown report
static/                dashboard and elder-phone pages (vanilla JS)
tests/                 pytest: scenarios, rule engine, LLM merge policy
docs/                  architecture, test report, demo script, LinkedIn post
```

## Main API endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/caregiver/patients/{id}` | Full profile: schedule, contacts, consent, rules (with source IDs/URLs) |
| GET | `/api/caregiver/patients/{id}/logs?days=7` | Dose logs |
| POST | `/api/agent/checkin` | Start a check-in `{patient_id, slot}` |
| POST | `/api/phone/sessions/{id}/answer` · `/reply` · `/hangup` · `/decline` | Elder's side of the call |
| GET | `/api/sessions/{id}` · `/api/sessions/{id}/trace` | Transcript, records and step-by-step trace |
| POST | `/api/approvals/{id}/decide` | Approve or reject a queued action |
| POST | `/api/tests/run` · GET `/api/tests/latest/report.md` | Run scenarios, download report |
| GET | `/api/llm/check` | Confirm Gemini works on the deployed app |
