"""Deterministic rule engine (English + Hindi, Devanagari and romanised).

Two jobs:
1. Safety guardrails that ALWAYS run, even when the LLM is available: emergency signs,
   symptoms, requests for medical advice, reported double doses. The final decision is the
   union of rule flags and LLM flags, so the LLM can add a safety flag but never remove one.
2. A complete fallback classifier for dose status, so the agent keeps working with no API key
   or when the LLM call fails or times out.
"""
import re
import unicodedata

DEVANAGARI = re.compile(r"[ऀ-ॿ]")

ROMAN_HINDI_HINTS = {
    "haan", "haa", "han", "nahi", "nahin", "nhi", "dawai", "dawa", "goli", "li", "liya", "kha", "maine",
    "mujhe", "main", "kya", "ji", "theek", "thik", "subah", "shaam", "chakkar", "accha", "achha", "bhool",
    "gayi", "hai", "hoon", "hu", "raha", "rahi", "aaj", "abhi", "kar", "dard", "tabiyat", "sakti", "sakta",
}


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", text or "")
    t = t.replace("’", "'").replace("‘", "'").replace("।", ".")  # danda → full stop
    t = t.lower()
    t = re.sub(r"[\"“”!?]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def detect_language(text: str) -> str:
    if DEVANAGARI.search(text or ""):
        return "hi"
    tokens = re.findall(r"[a-z']+", (text or "").lower())
    hits = sum(1 for tok in tokens if tok in ROMAN_HINDI_HINTS)
    return "hi" if hits >= 2 or (tokens and hits / max(len(tokens), 1) >= 0.4) else "en"


class Matcher:
    """Matches romanised patterns as word-bounded regex and Devanagari patterns as substrings
    (Devanagari vowel signs are not \\w, so \\b does not work inside Devanagari words)."""

    def __init__(self, patterns):
        self.items = []  # (label, kind, compiled_or_str)
        for label, pats in patterns.items():
            for p in pats:
                if DEVANAGARI.search(p):
                    self.items.append((label, "sub", p))
                else:
                    self.items.append((label, "re", re.compile(r"(?<![a-z])" + p + r"(?![a-z])")))

    def find(self, text: str):
        out = []
        for label, kind, pat in self.items:
            if kind == "sub":
                start = text.find(pat)
                while start != -1:
                    out.append((label, start, start + len(pat)))
                    start = text.find(pat, start + 1)
            else:
                for m in pat.finditer(text):
                    out.append((label, m.start(), m.end()))
        return out

    def any(self, text: str) -> bool:
        return bool(self.find(text))


NEG_BEFORE = re.compile(r"(?<![a-z])(no|not|never|without|nothing|neither|nor|zero|[a-z]+n't|dont|didnt|havent|hasnt|cant|wont)(?![a-z])[^.,;]{0,18}$")
NEG_AFTER = re.compile(r"^[^.,;]{0,14}(nahi|nahin|nhi|नहीं|नही|नहि)")


def _negated(text: str, start: int, end: int) -> bool:
    return bool(NEG_BEFORE.search(text[max(0, start - 30):start]) or NEG_AFTER.search(text[end:end + 22]))


EMERGENCY = Matcher({
    "chest pain": [r"chest pain", r"pain in (my |the )?chest", r"seene (me|mein|main) dard", r"chhati (me|mein) dard",
                   "सीने में दर्द", "छाती में दर्द", "सीने में दरद", r"heart attack"],
    "breathing difficulty": [r"can'?t breathe", r"cannot breathe", r"can not breathe", r"unable to breathe",
                             r"(difficulty|trouble|hard|problem) (in )?breathing", r"short(ness)? of breath",
                             r"breathless", r"saans (nahi|nahin|lene me|lene mein|phool|ruk)",
                             "सांस नहीं", "साँस नहीं", "सांस फूल", "साँस फूल", "सांस लेने में", "साँस लेने में"],
    "fainting / unconscious": [r"faint(ed|ing)?", r"passed out", r"unconscious", r"blacked out", r"behosh",
                               "बेहोश"],
    "fall": [r"i fell(?! asleep)", r"fell down", r"had a fall", r"slipped and fell", r"gir (gayi|gaya|gai|padi|pada)",
             "गिर गई", "गिर गया", "गिर पड़ी", "गिर पड़ा"],
    "stroke signs": [r"stroke", r"face (is )?drooping", r"slurred", r"can'?t move (my )?(arm|leg|hand)", r"numb(ness)?",
                     r"lakwa", "लकवा"],
    "heavy bleeding": [r"bleeding", r"khoon (beh|aa|nikal)", "खून बह", "खून आ"],
    "possible overdose": [r"overdose", r"(took|taken|swallowed) (too many|a lot of|all (the|my)|the whole (strip|bottle))",
                          r"saari goli", "सारी गोली", "बहुत सारी गोली"],
})

SYMPTOMS = Matcher({
    "dizziness": [r"dizz(y|iness)", r"giddy", r"giddiness", r"light ?headed", r"vertigo", r"chakkar", r"chakar",
                  r"head (is )?spinning", r"room (is )?spinning", "चक्कर"],
    "nausea / vomiting": [r"nause(a|ous)", r"vomit(ing|ed|s)?", r"threw up", r"throwing up", r"ulti", r"ji machal",
                          "उल्टी", "जी मिचल"],
    "headache": [r"headache", r"head ?ache", r"head (is )?(paining|hurting)", r"sir ?(me|mein)? ?dard",
                 "सिर दर्द", "सिर में दर्द", "सर दर्द", "सर में दर्द"],
    "weakness / fatigue": [r"weak(ness)?", r"tired(ness)?", r"fatigue(d)?", r"exhausted", r"no energy",
                           r"kamzor(i|ee)?", r"thakan", r"thaka(n|vat)", "कमज़ोरी", "कमजोरी", "थकान", "कमजोर"],
    "fever": [r"fever(ish)?", r"temperature", r"bukhar", r"bukhaar", "बुखार", "बुख़ार"],
    "stomach upset": [r"stomach ?(pain|ache|upset|problem)", r"stomachache", r"diarrh(o)?ea", r"loose motions?",
                      r"acidity", r"gas problem", r"pet (me|mein)? ?dard", "पेट दर्द", "पेट में दर्द", "दस्त"],
    "rash / itching": [r"rash(es)?", r"itch(ing|y)?", r"khujli", r"hives", "खुजली", "दाने"],
    "swelling": [r"swelling", r"swollen", r"sujan", r"soojan", "सूजन"],
    "vision problems": [r"blurr(ed|y) vision", r"can'?t see (clearly|properly)", r"vision (is )?blurr(ed|y)",
                        r"dhundhla", "धुंधला"],
    "confusion": [r"confus(ed|ion)", r"disoriented"],
    "drowsiness": [r"drowsy", r"too sleepy", r"very sleepy", r"neend (bahut|zyada)", "बहुत नींद"],
    "shakiness / sweating": [r"shak(y|ing)", r"trembl(e|ing)", r"sweat(ing|y)", r"pasina", r"kaanp", "पसीना", "कांप", "काँप"],
    "palpitations / anxiety": [r"palpitations?", r"heart (is )?(racing|pounding|beating fast)", r"ghabrahat", r"ghabrahat",
                               r"anxious", "घबराहट", "धड़कन तेज"],
    "low blood sugar signs": [r"low sugar", r"sugar (is )?low", r"hypo(glycemia)?", r"sugar kam", "शुगर कम"],
    "pain": [r"pain(ful)?", r"(body|joint|leg|back|knee|hand) ?(pain|ache)", r"aches?", r"hurts?", r"dard", "दर्द"],
    "cough / cold": [r"cough(ing)?", r"khansi", r"zukam", "खांसी", "खाँसी", "जुकाम", "ज़ुकाम"],
})

# Phrases that describe feeling unwell where "not" is part of the meaning (exempt from negation).
UNWELL = Matcher({
    "feeling unwell": [r"(don'?t|do not|not|am not|i'?m not) feel(ing)? (well|good|ok|okay|great|right|fine)",
                       r"not (well|good|ok|okay|fine)(?! bad)", r"(feeling|feel) (unwell|sick|bad|ill|terrible|awful|low)",
                       r"unwell", r"tabiyat (theek|thik|sahi) (nahi|nahin)", r"tabiyat (kharab|kharaab)",
                       r"theek nahi", r"thik nahi", "तबीयत ठीक नहीं", "तबियत ठीक नहीं", "तबीयत खराब", "तबियत खराब",
                       "ठीक नहीं लग"],
})

ADVICE = Matcher({
    "advice": [r"(should|shall|can|could|may|must) i", r"is it (ok|okay|safe|fine|alright|all right|good|bad)",
               r"do i (need|have) to", r"what (should|can|do) i (take|do|have)", r"how (much|many) (should|can|do|to)",
               r"(double|increase|reduce|halve|lower|raise) (the |my |this )?(dose|dosage|tablets?|pills?|medicines?|medication)",
               r"(double|increase|reduce) (it|them)", r"take (two|2|extra|another|more|double|half)",
               r"extra (dose|tablet|pill|medicine)", r"skip (the |my |it|them|tomorrow|today|tonight|a dose)",
               r"stop (taking|the|my|it)", r"instead of", r"any medicine for", r"which medicine",
               r"kya (main|mai|mein|mujhe|me)", r"(double|dugna|doguna|dugni) (kar|le|loon|lu|lun)",
               r"(dawai|dawa|goli) (chhod|chod|band|kam|zyada|jyada|badha)[a-z ]{0,10}(du|doon|dun|lu|loon|lun|de|sakti|sakta|chahiye)", r"(kitni|kitna|kitne) (goli|dawai|dawa)",
               r"(le|kha|lu|loon|lun) (sakti|sakta|sakte)", r"chhod (du|doon|dun)", r"band kar (du|doon|dun)",
               "क्या मैं", "क्या मुझे", "क्या मै ", "डबल कर", "दोगुनी", "दुगनी", "दोगुना", "दुगना", "छोड़ दूं",
               "छोड़ दूँ", "बंद कर दूं", "बंद कर दूँ", "कितनी गोली", "कितनी दवा", "ले सकती", "ले सकता",
               "खा सकती", "खा सकता", "ज़्यादा ले", "ज्यादा ले"],
})

DOUBLE_DOSE = Matcher({
    "double": [r"(took|taken|had|swallowed)(?![a-z])[^.,;?]{0,30}?(?<![a-z])(twice|two times|2 times|again|double|one more|another one|an extra)",
               r"(took|taken|had) (two|2|double|extra) (tablets?|pills?|doses?)", r"double dosed",
               r"do (baar|bar) (le|kha)", r"dobara (le|kha) (li|liya)", r"double (le|kha) (li|liya)",
               r"phir se (le|kha) (li|liya)", "दो बार ले", "दो बार खा", "दोबारा ले ली", "दोबारा खा ली",
               "डबल ले ली", "फिर से ले ली", "फिर से खा ली"],
})

SCHEDULE_CHANGE = Matcher({
    "change": [r"doctor (has |had )?(stopped|changed|reduced|increased|discontinued|removed|replaced|added|cancelled)",
               r"doctor (told|asked|said) (me )?to (stop|change|reduce|increase|skip)",
               r"(dr\.?|doctor) [a-z. ]{0,20}(stopped|changed|reduced|increased|discontinued)",
               r"(doctor|daktar|dr) ne [a-z ]{0,30}(band|badal|kam|chhod|chod|hata)",
               "डॉक्टर ने", "डाक्टर ने"],
})
SCHEDULE_CHANGE_VERB_HI = ("बंद", "बदल", "कम", "छोड़", "हटा")

UNSURE = Matcher({
    "unsure": [r"not sure", r"don'?t remember", r"do not remember", r"can'?t remember", r"cannot remember",
               r"don'?t know", r"i forget if", r"maybe", r"i think so", r"probably", r"pata nahi", r"pata nahin",
               r"yaad nahi", r"yaad nahin", r"shayad", "पता नहीं", "याद नहीं", "शायद"],
})

STRONG_POS = Matcher({
    "pos": [r"took", r"taken", r"have had", r"had (it|them|my|the|both|all)", r"swallowed", r"finished",
            r"already (had|took|taken|done)", r"done", r"le li", r"le liya", r"le chuki", r"le chuka", r"kha li",
            r"kha liya", r"kha chuki", r"kha chuka", r"li hai", r"liya hai", r"le lee", r"(goli|goliyan|dawai|dawa|tablet) (li|lee|khayi|khai)",
            "गोली ली", "गोलियाँ ली", "दवा ली", "दवाई ली",
            "ले ली", "ले लिया", "ले चुकी", "ले चुका", "खा ली", "खा लिया", "खा चुकी", "खा चुका", "ली है", "लिया है"],
})
STRONG_NEG = Matcher({
    "neg": [r"didn'?t", r"did not", r"haven'?t", r"have not", r"hasn'?t", r"not yet", r"not taken", r"not take",
            r"not took", r"forgot", r"forgotten", r"forget", r"missed", r"skipped", r"ran out", r"finished (the )?(strip|stock)",
            r"nahi li", r"nahin li", r"nhi li", r"nahi liya", r"nahi khayi", r"nahi khai", r"bhool", r"bhul gayi", r"bhul gaya",
            r"abhi nahi", r"abhi tak nahi", r"khatam ho",
            "नहीं ली", "नहीं लिया", "नहीं खाई", "नहीं खायी", "भूल", "अभी नहीं", "अभी तक नहीं", "खत्म हो", "ख़त्म हो"],
})
YES_OPEN = re.compile(r"^(yes|yeah|yep|yup|ya|yah|haan|haa|han|ha|ji haan|ji|of course|sure|definitely|i did|i have|correct|right)(?![a-z])")
YES_OPEN_HI = ("हाँ", "हां", "जी हाँ", "जी हां", "जी", "हा ")
NO_OPEN = re.compile(r"^(no|nope|nah|nahi|nahin|nhi|nai|not really)(?![a-z])")
NO_OPEN_HI = ("नहीं", "नही", "ना ")

WELL = Matcher({
    "good": [r"fine", r"good", r"well", r"ok", r"okay", r"alright", r"all right", r"great", r"better", r"perfect",
             r"no problems?", r"nothing", r"all good", r"theek", r"thik", r"accha", r"achha", r"acha", r"badhiya",
             r"mast", r"sab (theek|thik|badhiya)", "ठीक", "अच्छा", "अच्छी", "बढ़िया", "सब ठीक", "मस्त"],
})

ONLY = re.compile(r"(?<![a-z])(only|just|sirf|keval|bas)(?![a-z])|सिर्फ|केवल|बस ")
CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|\.|\bbut\b|\bexcept\b|\bhowever\b|\blekin\b|\bpar\b|\bmagar\b|लेकिन|मगर| पर |\bwhile\b)\s*")


NOT_ABOUT_DOSE = re.compile(r"^(no|not|koi|kuch)? ?(problems?|issues?|worries|tension|pain|dizziness|complaints?|bad|dikkat|pareshani|takleef)(?![a-z])")


def _polarity(clause: str, first: bool = True) -> str | None:
    """Return 'taken', 'not_taken', 'unclear', 'mixed' or None for a single clause.
    Bare yes/no openers only count in the first clause (the direct answer to the question)."""
    if NOT_ABOUT_DOSE.search(clause) or (SYMPTOMS.any(clause) and not STRONG_POS.any(clause) and not STRONG_NEG.any(clause)):
        return None
    if UNSURE.any(clause):
        return "unclear"
    pos = any(not _negated(clause, a, b) for _l, a, b in STRONG_POS.find(clause))
    neg = STRONG_NEG.any(clause)
    if neg and not pos:
        return "not_taken"
    if pos and not neg:
        return "taken"
    if pos and neg:
        # "took X, not Y" inside one clause → leave to per-med logic / unclear
        return "mixed"
    if not first:
        return None
    if YES_OPEN.search(clause) or clause.startswith(YES_OPEN_HI):
        return "taken"
    if NO_OPEN.search(clause) or clause.startswith(NO_OPEN_HI):
        return "not_taken"
    return None


def _med_mentions(clause: str, meds: list[dict]) -> list[str]:
    found = []
    for m in meds:
        names = [m["name"].lower()] + [a.lower() for a in (m.get("aliases") or [])]
        for n in names:
            if DEVANAGARI.search(n):
                hit = n in clause
            else:
                hit = re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", clause) is not None
            if hit:
                found.append(m["id"])
                break
    return found


def _symptoms(text: str) -> list[str]:
    labels = []
    for label, s, e in SYMPTOMS.find(text):
        if not _negated(text, s, e) and label not in labels:
            labels.append(label)
    if UNWELL.any(text) and "feeling unwell" not in labels:
        labels.append("feeling unwell")
    # "pain" is generic; drop it when a more specific pain label exists.
    if "pain" in labels and any(x in labels for x in ("headache", "stomach upset")):
        labels.remove("pain")
    return labels


def _emergencies(text: str) -> list[str]:
    out = []
    for label, s, e in EMERGENCY.find(text):
        if not _negated(text, s, e) and label not in out:
            out.append(label)
    return out


def safety_flags(text: str) -> dict:
    t = normalize(text)
    double = DOUBLE_DOSE.any(t)
    advice = ADVICE.any(t)
    sched = SCHEDULE_CHANGE.any(t)
    if sched and DEVANAGARI.search(t) and not any(v in t for v in SCHEDULE_CHANGE_VERB_HI):
        sched = bool(re.search(r"(doctor|daktar|dr) ne", t))
    return {
        "emergency_signs": _emergencies(t),
        "symptoms": _symptoms(t),
        "advice_request": advice,
        "double_dose": double,
        "schedule_change": sched,
    }


def classify(text: str, due_meds: list[dict], phase: str = "dose") -> dict:
    t = normalize(text)
    lang = detect_language(text)
    flags = safety_flags(text)

    per_med = {m["id"]: "unclear" for m in due_meds}
    dose_status = "none"
    confidence = 0.4

    clauses = [c for c in CLAUSE_SPLIT.split(t) if c]
    general = []  # polarities of clauses with no med mentioned
    mentioned_any = False
    for i, c in enumerate(clauses):
        pol = _polarity(c, first=(i == 0))
        mids = _med_mentions(c, due_meds)
        if mids:
            mentioned_any = True
            for mid in mids:
                if pol in ("taken", "not_taken", "unclear"):
                    per_med[mid] = pol
        elif pol:
            general.append(pol)

    gen = None
    if general:
        known = [g for g in general if g in ("taken", "not_taken")]
        if "unclear" in general and not known:
            gen = "unclear"
        elif known and all(g == known[0] for g in known):
            gen = known[0]
        elif known:
            gen = "mixed"
        else:
            gen = "mixed" if "mixed" in general else None
    if gen in ("taken", "not_taken", "unclear"):
        for mid, st in per_med.items():
            if st == "unclear" and not (mentioned_any and gen == "taken" and _only_specific(clauses, due_meds)):
                per_med[mid] = gen

    # "I took the sugar tablet only" / "sirf BP ki goli li" → the medicines not named were not taken
    if mentioned_any and ONLY.search(t) and "taken" in per_med.values():
        for mid, st in per_med.items():
            if st == "unclear":
                per_med[mid] = "not_taken"

    vals = set(per_med.values())
    has_dose_signal = mentioned_any or gen is not None
    if not due_meds:
        dose_status = "none"
    elif not has_dose_signal:
        dose_status = "none"
    elif vals == {"taken"}:
        dose_status, confidence = "taken", 0.85
    elif vals == {"not_taken"}:
        dose_status, confidence = "not_taken", 0.85
    elif vals == {"unclear"}:
        dose_status, confidence = "unclear", 0.4
    elif "unclear" in vals:
        dose_status, confidence = "partial_unclear", 0.7
    else:
        dose_status, confidence = "partial", 0.8

    if flags["double_dose"] and dose_status in ("none", "unclear"):
        dose_status, confidence = "taken", 0.75
        per_med = {mid: "taken" for mid in per_med}

    if flags["emergency_signs"] or flags["symptoms"]:
        wellbeing = "unwell"
    elif WELL.any(t):
        wellbeing = "good"
    else:
        wellbeing = "unknown"

    return {
        "language": lang,
        "corrected_text": text.strip(),
        "english_translation": None,
        "dose_status": dose_status,
        "per_med": per_med,
        "symptoms": flags["symptoms"],
        "emergency": bool(flags["emergency_signs"]),
        "emergency_signs": flags["emergency_signs"],
        "advice_request": flags["advice_request"],
        "double_dose": flags["double_dose"],
        "schedule_change": {"detected": flags["schedule_change"], "med_id": _first_med(t, due_meds) if flags["schedule_change"] else None,
                            "description": text.strip() if flags["schedule_change"] else None},
        "wellbeing": wellbeing,
        "confidence": confidence,
        "source": "rules",
    }


def _only_specific(clauses, due_meds) -> bool:
    """True when a general 'yes' clause is just an opener before naming specific meds,
    e.g. 'yes, I took the BP tablet' — then unnamed meds stay unclear instead of 'taken'."""
    named = [c for c in clauses if _med_mentions(c, due_meds)]
    unnamed = [c for c in clauses if not _med_mentions(c, due_meds)]
    return bool(named) and all(len(c.split()) <= 2 for c in unnamed)


def _first_med(t: str, meds: list[dict]) -> str | None:
    ids = _med_mentions(t, meds)
    return ids[0] if ids else None


# ---------- Output guardrail: checks the agent's OWN speech before it is spoken ----------
FORBIDDEN_AGENT_ADVICE = [
    re.compile(p) for p in [
        r"\byes,? (you can|you should|go ahead)\b", r"\b(you can|you should|you may) (take|double|skip|stop|increase|reduce)\b",
        r"\btake (two|2|double|an extra|another|more)\b", r"\bdouble (the|your) dose\b(?! on your own)",
        r"\b(it is|it's) (safe|fine|okay|ok) to (take|double|skip|stop)\b", r"\bmg\b.*\binstead\b",
        r"दो गोली ले लीजिए", r"डबल ले लीजिए", r"ले सकती हैं", r"ले सकते हैं",
    ]
]


def agent_speech_is_safe(text: str) -> tuple[bool, list[str]]:
    hits = [p.pattern for p in FORBIDDEN_AGENT_ADVICE if p.search(text.lower())]
    return (not hits, hits)
