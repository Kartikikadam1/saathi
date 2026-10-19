"""Everything the agent SAYS comes from these reviewed bilingual templates (English + Hindi).
Each helper returns (text_in_call_language, english_text) so the caregiver always has an
English transcript. No LLM-generated text is ever spoken to the elder."""

SLOT_HI = {"morning": "सुबह", "evening": "शाम"}

MED_HI = {
    "metformin": "मेटफॉर्मिन (शुगर की गोली)",
    "amlodipine": "एम्लोडिपिन (बीपी की गोली)",
    "atorvastatin": "एटोरवास्टेटिन (कोलेस्ट्रॉल की गोली)",
}

SYMPTOM_HI = {
    "dizziness": "चक्कर", "nausea / vomiting": "जी मिचलाने या उल्टी", "headache": "सिर दर्द",
    "weakness / fatigue": "कमज़ोरी", "fever": "बुखार", "stomach upset": "पेट की तकलीफ़",
    "rash / itching": "खुजली या दाने", "swelling": "सूजन", "vision problems": "धुंधला दिखने",
    "confusion": "उलझन", "drowsiness": "ज़्यादा नींद", "shakiness / sweating": "काँपने या पसीने",
    "palpitations / anxiety": "घबराहट", "low blood sugar signs": "शुगर कम होने", "pain": "दर्द",
    "cough / cold": "खांसी-जुकाम", "feeling unwell": "तबीयत ठीक न होने",
}


def _join(items: list[str], lang: str) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    conj = " और " if lang == "hi" else " and "
    return ", ".join(items[:-1]) + conj + items[-1]


def med_names(meds: list[dict], lang: str) -> str:
    if lang == "hi":
        return _join([MED_HI.get(m["name"].lower(), m["name"]) for m in meds], "hi")
    return _join([f"{m['name']} {(m.get('dose') or '').split(',')[0]}".strip() for m in meds], "en")


def symptoms_text(labels: list[str], lang: str) -> str:
    if lang == "hi":
        return _join([SYMPTOM_HI.get(s, s) for s in labels], "hi")
    return _join([s.split(" / ")[0] for s in labels], "en")


def _pair(lang, hi, en):
    return (hi if lang == "hi" else en, en)


def greeting(lang, name, slot, meds, family):
    return _pair(lang,
                 f"नमस्ते {name} जी। मैं साथी हूँ, आपकी दवा की जाँच करने वाली सहायक, और {family} की ओर से बात कर रही हूँ। "
                 f"यह कॉल आपके परिवार के लिए रिकॉर्ड हो रही है। क्या आपने {SLOT_HI[slot]} की दवाइयाँ — {med_names(meds, 'hi')} — ले ली हैं?",
                 f"Namaste {name} ji. This is Saathi, your medicine check-in assistant, calling on behalf of {family}. "
                 f"This call is recorded for your family. Have you taken your {slot} medicines — {med_names(meds, 'en')}?")


def ack_taken(lang, meds):
    return _pair(lang, f"धन्यवाद, मैंने नोट कर लिया है कि आपने {med_names(meds, 'hi')} ले ली है।",
                 f"Thank you, I've noted that you took {med_names(meds, 'en')}.")


def ack_partial(lang, taken, missed):
    return _pair(lang, f"मैंने नोट किया कि आपने {med_names(taken, 'hi')} ले ली है, लेकिन {med_names(missed, 'hi')} नहीं ली।",
                 f"I've noted that you took {med_names(taken, 'en')}, but not {med_names(missed, 'en')}.")


def missed(lang, meds, doctor, family, include_ack=True, include_tail=True):
    hi_ack = f"ठीक है, मैंने नोट किया कि {med_names(meds, 'hi')} नहीं ली गई। " if include_ack else ""
    en_ack = f"Okay, I've noted that {med_names(meds, 'en')} {'was' if len(meds) == 1 else 'were'} not taken. " if include_ack else ""
    hi_tail = f"देर से लेनी है या नहीं, यह कृपया {doctor} से पूछ लें, और अपने आप से ज़्यादा दवा न लें। मैं {family} को बता रही हूँ।" if include_tail else ""
    en_tail = f"If you're unsure whether to take it late, please check with {doctor} — and please don't take extra on your own. I'm letting {family} know." if include_tail else ""
    return _pair(lang, (hi_ack + hi_tail).strip(), (en_ack + en_tail).strip())


def ask_specific(lang, meds):
    return _pair(lang, f"और क्या आपने {med_names(meds, 'hi')} भी ली? कृपया हाँ या ना में बताइए।",
                 f"And did you take {med_names(meds, 'en')} as well? Please say yes or no.")


def reask(lang, slot, meds):
    return _pair(lang, f"माफ़ कीजिए, मैं ठीक से समझ नहीं पाई। क्या आपने {SLOT_HI[slot]} की दवाइयाँ — {med_names(meds, 'hi')} — ले लीं? कृपया हाँ या ना में बताइए।",
                 f"Sorry, I didn't quite catch that. Did you take your {slot} medicines — {med_names(meds, 'en')}? Please say yes or no.")


def reask_after_other(lang, slot, meds):
    return _pair(lang, f"अब आज के बारे में — क्या आपने {SLOT_HI[slot]} की दवाइयाँ, {med_names(meds, 'hi')}, ले लीं?",
                 f"Now, about today — did you take your {slot} medicines, {med_names(meds, 'en')}?")


def uncertain_logged(lang, family):
    return _pair(lang, f"कोई बात नहीं। मैं इसे 'पुष्टि नहीं हुई' के रूप में दर्ज कर रही हूँ और {family} से आपसे बात करने को कहूँगी।",
                 f"That's alright. I'll mark this as unconfirmed and ask {family} to check with you.")


def wellness_q(lang):
    return _pair(lang, "आज आपकी तबीयत कैसी है? कोई चक्कर, दर्द या और कोई परेशानी?",
                 "How are you feeling today? Any dizziness, pain or other problems?")


def wellness_good(lang):
    return _pair(lang, "यह सुनकर अच्छा लगा।", "Glad to hear that.")


def advice_refusal(lang, doctor, family):
    return _pair(lang,
                 f"माफ़ कीजिए, मैं दवा के बारे में सलाह नहीं दे सकती। कृपया अपने आप से कोई भी खुराक न बदलें, न बढ़ाएँ, न छोड़ें — पहले {doctor} से पूछ लें। "
                 f"मैं {family} को आपके इस सवाल के बारे में बता दूँगी।",
                 f"I'm sorry, I'm not able to give medical advice. Please don't change, increase or skip any dose on your own — please ask {doctor} first. "
                 f"I'll let {family} know you had this question.")


def symptom_care(lang, labels, doctor, family):
    return _pair(lang,
                 f"यह सुनकर दुख हुआ कि आपको {symptoms_text(labels, 'hi')} की परेशानी है। कृपया बैठकर आराम करें, और इसके बारे में {doctor} से ज़रूर बात करें। "
                 f"मैं अभी {family} को बता रही हूँ।",
                 f"I'm sorry to hear about the {symptoms_text(labels, 'en')}. Please sit down and rest, and please speak to {doctor} about this. "
                 f"I'm informing {family} right now.")


def double_dose_care(lang, doctor, family):
    return _pair(lang,
                 f"बताने के लिए धन्यवाद। जब तक आप {doctor} से बात न कर लें, कृपया और कोई दवा न लें। मैं तुरंत {family} को बता रही हूँ।",
                 f"Thank you for telling me. Please don't take anything more until you have spoken to {doctor}. I'm informing {family} right away.")


def emergency(lang, family_all):
    return _pair(lang,
                 f"यह गंभीर हो सकता है। कृपया अभी 112 पर कॉल करें या पास में किसी से मदद माँगें। मैं तुरंत {family_all} को सूचित कर रही हूँ।",
                 f"This could be serious. Please call 112 right now, or ask someone near you for help. I'm alerting {family_all} immediately.")


def schedule_change_noted(lang, family):
    return _pair(lang,
                 f"धन्यवाद, मैंने नोट कर लिया है कि डॉक्टर ने आपकी दवा में बदलाव किया है। शेड्यूल बदलने से पहले {family} इसकी पुष्टि करेंगे। "
                 f"तब तक, कृपया वही करें जो डॉक्टर ने कहा है।",
                 f"Thank you, I've noted that your doctor changed your medicine. {family} will confirm it before your schedule is updated. "
                 f"Until then, please follow what your doctor told you.")


def closing(lang, name):
    return _pair(lang, f"धन्यवाद {name} जी। अपना ध्यान रखिए, अगली दवा के समय मैं फिर से कॉल करूँगी। नमस्ते!",
                 f"Thank you, {name} ji. Take care, and I'll call again at your next medicine time. Goodbye!")
