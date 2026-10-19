"""Rule engine: dose understanding and safety guardrails in English, Hindi and Hinglish."""
import pytest

from app.agent import rules

MEDS = [{"id": "A", "name": "Metformin", "aliases": ["sugar", "sugar ki goli", "शुगर"]},
        {"id": "B", "name": "Amlodipine", "aliases": ["bp", "bp ki goli", "बीपी"]}]


@pytest.mark.parametrize("text,status", [
    ("Yes", "taken"), ("Yes, took morning tablets", "taken"), ("Yes I took them, no problems", "taken"),
    ("No, not yet", "not_taken"), ("I haven't taken them", "not_taken"), ("I forgot", "not_taken"),
    ("haan maine dawai le li", "taken"), ("nahi, bhool gayi", "not_taken"), ("हाँ, मैंने दवा ले ली", "taken"),
    ("नहीं, अभी तक नहीं ली", "not_taken"), ("I took the BP tablet but forgot the sugar tablet", "partial"),
    ("मैंने बीपी की गोली ले ली पर शुगर की नहीं ली", "partial"), ("I took the BP one", "partial_unclear"),
    ("I took the sugar tablet only", "partial"), ("sirf BP ki goli li", "partial"), ("I'm not sure", "unclear"), ("Should I double the dose?", "none"), ("I am fine, no dizziness", "none"),
])
def test_dose_status(text, status):
    assert rules.classify(text, MEDS)["dose_status"] == status


@pytest.mark.parametrize("text,flag", [
    ("Should I double the dose?", "advice_request"), ("Can I skip the evening tablet?", "advice_request"),
    ("Kya main do goli le sakti hoon?", "advice_request"), ("क्या मैं दवा दोगुनी कर लूँ?", "advice_request"),
    ("I think I took my tablets twice", "double_dose"), ("I have chest pain", "emergency"),
    ("सांस नहीं ले पा रही", "emergency"), ("I fell down in the bathroom", "emergency"),
])
def test_safety_flags(text, flag):
    assert rules.classify(text, MEDS)[flag]


@pytest.mark.parametrize("text,expected", [
    ("I'm feeling dizzy", ["dizziness"]), ("chakkar aa raha hai", ["dizziness"]), ("सिर में दर्द है", ["headache"]),
    ("no dizziness, I'm fine", []), ("mujhe chakkar nahi aa raha", []), ("I don't feel well", ["feeling unwell"]),
])
def test_symptoms_respect_negation(text, expected):
    assert rules.classify(text, MEDS)["symptoms"] == expected


def test_not_emergency_when_negated_or_benign():
    assert not rules.classify("no chest pain", MEDS)["emergency"]
    assert not rules.classify("I fell asleep early", MEDS)["emergency"]


def test_language_detection():
    assert rules.detect_language("हाँ ले ली") == "hi"
    assert rules.detect_language("haan maine dawai le li") == "hi"
    assert rules.detect_language("yes I took them") == "en"


@pytest.mark.parametrize("line", ["Yes, you can take two tablets.", "It's safe to double the dose.", "You should skip it today."])
def test_output_guardrail_blocks_advice(line):
    ok, _ = rules.agent_speech_is_safe(line)
    assert not ok


def test_output_guardrail_allows_templates():
    from app.agent import responses as R
    for text, en in [R.advice_refusal("en", "Dr. X", "Rahul"), R.missed("en", [{"name": "Metformin", "dose": "500 mg"}], "Dr. X", "Rahul"),
                     R.double_dose_care("en", "Dr. X", "Rahul"), R.symptom_care("en", ["dizziness"], "Dr. X", "Rahul")]:
        assert rules.agent_speech_is_safe(en)[0], en
