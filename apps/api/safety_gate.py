import re
from dataclasses import dataclass

EMERGENCY_REPLY = "This is an automated system. If you are experiencing a medical emergency, please call local emergency services immediately or go to the nearest hospital."
MEDICAL_ADVICE_REPLY = "I am an AI assistant and cannot provide medical advice. Please consult a doctor for diagnosis or treatment options."

CHEST_PAIN_CARDIAC = "chest_pain_cardiac"
BREATHING_DISTRESS = "breathing_distress"
STROKE_SYMPTOMS = "stroke_symptoms"
SEVERE_BLEEDING_TRAUMA = "severe_bleeding_trauma"
SELF_HARM_CRISIS = "self_harm_crisis"
MEDICAL_ADVICE_REQUEST = "medical_advice_request"

EMERGENCY_CATEGORIES = (
    CHEST_PAIN_CARDIAC,
    BREATHING_DISTRESS,
    STROKE_SYMPTOMS,
    SEVERE_BLEEDING_TRAUMA,
    SELF_HARM_CRISIS,
)


@dataclass
class GateResult:
    triggered: bool
    category: str | None
    reply_text: str | None


_PATTERN_SOURCES: dict[str, list[str]] = {
    CHEST_PAIN_CARDIAC: [
        r"chest\s+pain",
        r"chest.*(tight|tightness)",
        r"pain in (my|the) chest",
        r"chest.*(pressure|hurt|hurting)",
        r"crushing\s+(chest\s+)?pain",
        r"heart attack",
        r"my heart hurts",
        r"pain (radiating|spreading) (down|to) my arm",
        r"सीने में दर्द",
        r"छाती में दर्द",
        r"दिल का दौरा",
        r"सीने में जकड़न",
        r"seene mein dard",
        r"chhaati mein dard",
        r"dil ka dora",
        r"dil ka daura",
        r"seene mein jakdan",
        r"ghabrahat",
        r"heart attack aa raha",
    ],
    BREATHING_DISTRESS: [
        r"can'?t breathe",
        r"cannot breathe",
        r"can'?t catch (my|her|his|their) breath",
        r"hard(ly)? time breathing",
        r"difficulty breathing",
        r"short(ness)? of breath",
        r"gasping for air",
        r"choking",
        r"suffocating",
        r"सांस नहीं आ रही",
        r"सांस लेने में तकलीफ",
        r"दम घुट रहा",
        r"saans nahi aa rahi",
        r"saans lene mein takleef",
        r"dum ghut raha",
        r"saans phool rahi",
        r"saans ful rahi",
    ],
    STROKE_SYMPTOMS: [
        r"face.*droop",
        r"slurred speech",
        r"can'?t speak (properly|right)",
        r"numb(ness)?.*(one side|half)",
        r"can'?t move (my|his|her)? ?(arm|leg|face)",
        r"sudden.*confusion",
        r"sudden.*severe headache",
        r"can'?t see (properly|clearly)",
        r"sudden vision loss",
        r"can'?t feel my arm",
        r"चेहरा टेढ़ा",
        r"बोलने में दिक्कत",
        r"शरीर.*सुन्न",
        r"chehra tedha",
        r"bolne mein dikkat",
        r"sharir.*sunn",
        r"lakwa",
        r"paralysis maar gaya",
    ],
    SEVERE_BLEEDING_TRAUMA: [
        r"bleeding (a lot|heavily|badly)",
        r"won'?t stop bleeding",
        r"can'?t stop the bleeding",
        r"unconscious",
        r"unresponsive",
        r"passed out",
        r"severe injury",
        r"lost a lot of blood",
        r"deep cut",
        r"बहुत खून बह रहा",
        r"बेहोश हो गया",
        r"bahut khoon beh raha",
        r"behosh ho gaya",
        r"khoon nahi ruk raha",
        r"chakkar kha ke gir gaya",
    ],
    SELF_HARM_CRISIS: [
        r"suicidal",
        r"want to (kill|hurt) myself",
        r"end my life",
        r"self[\s-]?harm",
        r"hurting myself",
        r"don'?t want to live",
        r"thinking about suicide",
        r"no reason to live",
        r"can'?t go on",
        r"want to disappear",
        r"better off dead",
        r"can'?t do this anymore",
        r"give up on life",
        r"want to end it all",
        r"overdose",
        r"cutting myself",
        r"आत्महत्या",
        r"खुद को नुकसान",
        r"जीना नहीं चाह(ता|ती)",
        r"मरना चाह(ता|ती) हूं",
        r"aatmahatya",
        r"khudkushi",
        r"khud ko nuksan",
        r"jeena nahi chah(ta|ti)",
        r"marna chah(ta|ti) h(oon|un)",
        r"jaan de dunga",
        r"jaan de dungi",
        r"nas kaat",
    ],
    MEDICAL_ADVICE_REQUEST: [
        r"what medicine should i take",
        r"what dose",
        r"how many (pills|tablets)",
        r"is it safe to take",
        r"can i take .* with",
        r"diagnos",
        r"what'?s wrong with me",
        r"do i have (a |an )?(infection|disease|cancer|covid)",
        r"prescribe",
        r"what should i take for",
        r"कौन सी दवा",
        r"मुझे क्या हुआ है",
        r"kaun si dawa",
        r"mujhe kya hua hai",
        r"kya dawai lu",
        r"dose kitni",
    ],
}

_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    category: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for category, patterns in _PATTERN_SOURCES.items()
}


def _matches(category: str, text: str) -> bool:
    return any(pattern.search(text) for pattern in _PATTERNS[category])


def action_for_category(category: str) -> str:
    return "escalated" if category in EMERGENCY_CATEGORIES else "deflected"


def evaluate(text: str) -> GateResult:
    for category in EMERGENCY_CATEGORIES:
        if _matches(category, text):
            return GateResult(triggered=True, category=category, reply_text=EMERGENCY_REPLY)

    if _matches(MEDICAL_ADVICE_REQUEST, text):
        return GateResult(
            triggered=True, category=MEDICAL_ADVICE_REQUEST, reply_text=MEDICAL_ADVICE_REPLY
        )

    return GateResult(triggered=False, category=None, reply_text=None)
