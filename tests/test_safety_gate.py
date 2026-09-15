import pytest
from safety_gate import (
    BREATHING_DISTRESS,
    CHEST_PAIN_CARDIAC,
    EMERGENCY_REPLY,
    MEDICAL_ADVICE_REPLY,
    MEDICAL_ADVICE_REQUEST,
    SELF_HARM_CRISIS,
    SEVERE_BLEEDING_TRAUMA,
    STROKE_SYMPTOMS,
    compose_reply,
    evaluate,
)


@pytest.mark.parametrize(
    ("text", "expected_category"),
    [
        ("I have really bad chest pain right now", CHEST_PAIN_CARDIAC),
        ("my chest feels so tight and heavy", CHEST_PAIN_CARDIAC),
        ("I think I'm having a heart attack", CHEST_PAIN_CARDIAC),
        ("I can't breathe properly", BREATHING_DISTRESS),
        ("having a hard time breathing since this morning", BREATHING_DISTRESS),
        ("she is gasping for air", BREATHING_DISTRESS),
        ("his face is drooping on one side", STROKE_SYMPTOMS),
        ("I have slurred speech all of a sudden", STROKE_SYMPTOMS),
        ("my arm is bleeding heavily and won't stop", SEVERE_BLEEDING_TRAUMA),
        ("he is unconscious and not responding", SEVERE_BLEEDING_TRAUMA),
        ("I don't want to live anymore", SELF_HARM_CRISIS),
        ("I have been thinking about suicide", SELF_HARM_CRISIS),
        ("what dose of ibuprofen should I take", MEDICAL_ADVICE_REQUEST),
        ("can you diagnose what's wrong with me", MEDICAL_ADVICE_REQUEST),
        ("what medicine should I take for a fever", MEDICAL_ADVICE_REQUEST),
        ("mujhe bahut ghabrahat ho rahi hai", CHEST_PAIN_CARDIAC),
        ("uska saans phool rahi hai", BREATHING_DISTRESS),
        ("usko lakwa mar gaya hai", STROKE_SYMPTOMS),
        ("uska khoon nahi ruk raha hai", SEVERE_BLEEDING_TRAUMA),
        ("main khudkushi karna chahta hoon", SELF_HARM_CRISIS),
        ("kya dawai lu bukhar ke liye", MEDICAL_ADVICE_REQUEST),
    ],
)
def test_ambiguous_and_direct_language_triggers_expected_category(
    text: str, expected_category: str
) -> None:
    result = evaluate(text)
    assert result.triggered is True
    assert result.category == expected_category


@pytest.mark.parametrize(
    "text",
    [
        "I'd like to book an appointment for a checkup",
        "What are your clinic hours on Saturday",
        "Can I reschedule my dental cleaning to next week",
        "Hi, I want to register as a new patient",
        "Thanks so much, see you then",
    ],
)
def test_ordinary_booking_language_does_not_trigger(text: str) -> None:
    result = evaluate(text)
    assert result.triggered is False
    assert result.category is None
    assert result.reply_text is None


def test_emergency_categories_use_emergency_reply() -> None:
    result = evaluate("I have crushing chest pain")
    assert result.reply_text == EMERGENCY_REPLY


def test_medical_advice_category_uses_medical_advice_reply() -> None:
    result = evaluate("what medicine should I take for a fever")
    assert result.reply_text == MEDICAL_ADVICE_REPLY


def test_emergency_language_takes_priority_over_medical_advice_language() -> None:
    result = evaluate("I have chest pain, what medicine should I take")
    assert result.category == CHEST_PAIN_CARDIAC
    assert result.reply_text == EMERGENCY_REPLY


def test_compose_reply_appends_escalation_rule() -> None:
    result = compose_reply(EMERGENCY_REPLY, "Call the clinic's after-hours line.")
    assert result == f"{EMERGENCY_REPLY}\n\nCall the clinic's after-hours line."


def test_compose_reply_strips_surrounding_whitespace() -> None:
    result = compose_reply(EMERGENCY_REPLY, "  Call the clinic's after-hours line.  \n")
    assert result == f"{EMERGENCY_REPLY}\n\nCall the clinic's after-hours line."


@pytest.mark.parametrize("escalation_rule", [None, "", "   ", "\n\t"])
def test_compose_reply_falls_back_to_base_reply_when_escalation_rule_is_absent(
    escalation_rule: str | None,
) -> None:
    result = compose_reply(EMERGENCY_REPLY, escalation_rule)
    assert result == EMERGENCY_REPLY
