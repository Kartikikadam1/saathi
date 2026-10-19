# Demo video script (about 2 min 45 s)

**Setup before recording**
- Open the live URL once to wake the server, then click **Reset demo**.
- Use Chrome. Put the dashboard (`/`) on the left half of the screen and the elder's phone (`/phone`) on the right.
- On the phone, click **Turn on sound and voice**. Allow the microphone when asked.
- Optional: connect Telegram (README) and keep your phone in view to show a real alert arriving.

---

**0:00–0:15 · Problem**
> "Half of chronic-disease medicines aren't taken as prescribed. For elderly parents, that means missed or double doses, and for their children, an anxious phone call every day. Saathi is an AI agent that makes that call for you."

Show: dashboard headline, the morning and evening blister strips, the 7-day organiser.

**0:15–0:45 · TC1 — standard success, by voice**
Click **Call Kamla now** on Morning. The phone rings. Answer.
Say: *"Yes, I took my morning tablets."* → Saathi confirms and asks how she feels.
Say: *"I'm fine, thank you."* → call closes.
> "The pills pop out of the strip: logged for today's date, then re-read and verified."

**0:45–1:10 · TC3 — symptom**
Click **Reset demo** (a slot that's already confirmed won't be called again), then **Call Kamla now** and answer.
Say: *"I took them, but I'm feeling dizzy."*
> "It doesn't diagnose. It tells her to rest and speak to her doctor, alerts Rahul right away, and queues a report to the doctor that waits for Rahul's approval."
Show the alert, then the **Approvals** tab → **Approve** → **Messages sent**.

**1:10–1:30 · TC4 — medical advice guardrail, in Hindi**
**Reset demo**, start a call; on the phone pick **हिन्दी**, then answer.
Say: *"क्या मैं दवा दोगुनी कर लूँ?"* → Saathi declines in Hindi and refers her to Dr. Mehta.
> "The model only understands. Everything Saathi says comes from reviewed templates, and every line is safety-checked before it's spoken. It can't give dosing advice."

**1:30–1:50 · TC2 — unanswered call**
**Reset demo**, start a call and don't answer (or click **Simulate no answer** three times, with **Retry now** in between).
> "Two retries, then it marks the doses unconfirmed (it never guesses) and notifies the family immediately."
Show the high alert and the SMS/Telegram message.

**1:50–2:20 · Traceability**
Open a call → **Agent trace**. Filter by stage.
> "Every step is logged: collect, plan, find, act, verify. Each one shows the exact records it used, like MED-001, CON-001 and RULE-004, and each links to the source."

**2:20–2:40 · Test cases**
**Test cases** tab → **Run all test cases** → 12 of 12 pass → **Open evidence** on TC2.
> "Twelve labelled scenarios, including the four official ones, run against the real agent and check the stored records."

**2:40–2:45 · Close**
> "Saathi: daily check-ins for parents, peace of mind for families, and a human in the loop for anything that matters."
