# LinkedIn post (draft — edit to your voice, then add the screenshot + demo link)

Built for the Ziro Agentic AI Hackathon 2026: **Saathi**, an AI agent that calls elderly parents at medicine time 💊📞

The problem: about half of chronic-disease medicines aren't taken as prescribed, and for many families the only safety net is a worried daily phone call.

What Saathi does:
✅ Calls at medicine time and asks, in English or Hindi, whether each dose was taken
✅ Logs every answer to a caregiver dashboard, then re-reads and verifies each record
✅ No answer after 2 retries → alerts the family immediately
✅ Mentions dizziness → advises seeing the doctor and alerts the family
✅ "Should I double the dose?" → never gives medical advice and refers to the doctor
✅ Prescription changes and doctor reports wait for a human's approval
✅ Every decision is traceable to the exact records and rules it used

Under the hood: Python + FastAPI, Google Gemini for understanding Hindi and English (with a rule engine that can't be overruled on safety), the Web Speech API for voice, SQLite, and a step-by-step agent trace (Collect → Plan → Find → Act → Verify). 12 labelled test cases, including the 4 official ones, all passing.

🔗 Live demo: <your Render URL>
💻 Code: <your GitHub URL>

Thanks @Ziro Digital for the problem statement!

#AgenticAI #HealthcareAI #AI #Hackathon #ZiroDigital #GenAI #ElderCare #Python #FastAPI #Gemini
