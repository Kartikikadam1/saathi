"""Hybrid understanding: Gemini for nuance + rule engine for guarantees.

Merge policy
- Dose status: Gemini's answer when its confidence >= LLM_MIN_CONFIDENCE, else the rule engine's.
- Safety flags (emergency, symptoms, advice request, double dose, schedule change): UNION of both.
  The LLM can add a safety flag; it can never remove one the rules raised.
"""
from .. import config
from . import llm, rules


def _interpretation(c: dict) -> str:
    bits = [f"dose status: {c['dose_status']}"]
    if c["symptoms"]:
        bits.append("symptoms: " + ", ".join(c["symptoms"]))
    if c["emergency"]:
        bits.append("EMERGENCY: " + ", ".join(c["emergency_signs"]))
    if c["advice_request"]:
        bits.append("asked for medical advice")
    if c["double_dose"]:
        bits.append("reported a double dose")
    if c["schedule_change"]["detected"]:
        bits.append("reported a prescription change")
    return "[auto-interpreted] " + "; ".join(bits)


def understand(text: str, due_meds: list[dict], phase: str, question: str) -> dict:
    r = rules.classify(text, due_meds, phase)
    g = llm.classify(text, due_meds, phase, question) if config.llm_enabled() else None

    if g is None:
        out = dict(r)
        out["llm_used"] = False
        out["llm_note"] = "Gemini not configured" if not config.llm_enabled() else "Gemini call failed — rule engine used"
    else:
        use_llm_dose = g["confidence"] >= config.LLM_MIN_CONFIDENCE
        out = dict(g if use_llm_dose else r)
        out["corrected_text"] = g["corrected_text"]
        out["english_translation"] = g["english_translation"]
        out["language"] = "hi" if r["language"] == "hi" else g["language"]
        sym = list(dict.fromkeys(r["symptoms"] + [s.lower() for s in g["symptoms"]]))
        # Avoid duplicates such as rules 'dizziness' + llm 'dizzy'
        out["symptoms"] = _dedupe_symptoms(sym)
        out["emergency_signs"] = list(dict.fromkeys(r["emergency_signs"] + g["emergency_signs"]))
        out["emergency"] = r["emergency"] or g["emergency"]
        out["advice_request"] = r["advice_request"] or g["advice_request"]
        out["double_dose"] = r["double_dose"] or g["double_dose"]
        sc_r, sc_g = r["schedule_change"], g["schedule_change"]
        out["schedule_change"] = {"detected": sc_r["detected"] or sc_g["detected"],
                                  "med_id": sc_g["med_id"] or sc_r["med_id"],
                                  "description": sc_g["description"] or sc_r["description"]}
        if out["symptoms"] or out["emergency"]:
            out["wellbeing"] = "unwell"
        out["source"] = g["source"] if use_llm_dose else "rules (LLM confidence too low)"
        out["llm_used"] = True
        out["rules_view"] = {k: r[k] for k in ("dose_status", "per_med", "symptoms", "emergency", "advice_request", "double_dose")}
        out["llm_view"] = {k: g[k] for k in ("dose_status", "per_med", "symptoms", "emergency", "advice_request", "double_dose", "confidence")}

    if not out.get("english_translation"):
        out["english_translation"] = text if out["language"] == "en" else _interpretation(out)
    return out


def _dedupe_symptoms(labels: list[str]) -> list[str]:
    roots = []
    out = []
    for s in labels:
        root = s.split(" / ")[0].replace("iness", "y").replace("ness", "")[:5]
        if root in roots:
            continue
        roots.append(root)
        out.append(s)
    return out
