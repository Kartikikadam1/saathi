"""Gemini path, with the HTTP call mocked: safety flags are a union, so the LLM can add but never remove one."""
import json

from app import config
from app.agent import classifier, llm

MEDS = [{"id": "A", "name": "Metformin", "aliases": []}, {"id": "B", "name": "Amlodipine", "aliases": []}]


def _fake(payload):
    return lambda *a, **k: json.dumps(payload)


BASE = {"language": "en", "corrected_text": "", "english_translation": "", "dose_status": "taken",
        "per_med": [{"med_id": "A", "status": "taken"}, {"med_id": "B", "status": "taken"}], "symptoms": [],
        "emergency": False, "emergency_signs": [], "advice_request": False, "double_dose": False,
        "schedule_change_detected": False, "schedule_change_med_id": "", "wellbeing": "good", "confidence": 0.95}


def test_llm_cannot_remove_rule_safety_flag(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "_call", _fake(BASE))  # LLM says: no advice request
    out = classifier.understand("Yes I took them. Should I double the dose tomorrow?", MEDS, "dose", "q")
    assert out["advice_request"] is True and out["llm_used"] is True
    assert out["dose_status"] == "taken"


def test_llm_can_add_flag_and_translation(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    p = dict(BASE, symptoms=["blurred vision"], english_translation="I took it, my eyes feel blurry", wellbeing="unwell")
    monkeypatch.setattr(llm, "_call", _fake(p))
    out = classifier.understand("le li, aankhon se dhundla dikh raha", MEDS, "dose", "q")
    assert "blurred vision" in out["symptoms"]
    assert out["english_translation"] == "I took it, my eyes feel blurry"


def test_low_confidence_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "_call", _fake(dict(BASE, dose_status="taken", confidence=0.2)))
    out = classifier.understand("No, I forgot", MEDS, "dose", "q")
    assert out["dose_status"] == "not_taken"


def test_llm_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "x")
    monkeypatch.setattr(llm, "_call", lambda *a, **k: None)
    out = classifier.understand("Yes", MEDS, "dose", "q")
    assert out["dose_status"] == "taken" and out["llm_used"] is False
