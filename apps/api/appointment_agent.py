import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

import anthropic
import asyncpg
from pydantic import BaseModel, ConfigDict

from onboarding_agent import EXTRACTION_FAILURE_REPLY, EXTRACTION_TIMEOUT_SECONDS, MAX_UNCLEAR_ATTEMPTS

APPOINTMENT_MODEL = "claude-sonnet-5"
SLOT_SEARCH_CANDIDATE_COUNT = 3


class AppointmentStep(str, Enum):
    AWAITING_INTENT = "awaiting_intent"
    AWAITING_APPOINTMENT_SELECTION = "awaiting_appointment_selection"
    AWAITING_DATETIME = "awaiting_datetime"
    AWAITING_SLOT_SELECTION = "awaiting_slot_selection"
    AWAITING_CANCEL_CONFIRMATION = "awaiting_cancel_confirmation"


class IntentExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["book", "reschedule", "cancel", "unclear"]


class DateTimeExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    iso_datetime: str | None


class SelectionExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_number: int | None


class ConfirmationExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["affirmative", "decline", "unclear"]


class ExtractionFailure(Exception):
    pass


class AppointmentExtractor(Protocol):
    async def extract_intent(self, text: str) -> IntentExtraction: ...

    async def extract_datetime(self, text: str, reference_now: datetime) -> DateTimeExtraction: ...

    async def extract_selection(self, text: str, option_count: int) -> SelectionExtraction: ...

    async def extract_confirmation(self, text: str) -> ConfirmationExtraction: ...


@dataclass
class AppointmentState:
    step: AppointmentStep
    intent: Literal["book", "reschedule", "cancel"] | None
    target_appointment_id: uuid.UUID | None
    candidate_ids: list[uuid.UUID]
    unclear_count: int


@dataclass
class AppointmentPersistedChanges:
    booked_appointment_id: uuid.UUID | None = None
    cancelled_appointment_id: uuid.UUID | None = None
    rescheduled_from_appointment_id: uuid.UUID | None = None
    rescheduled_to_appointment_id: uuid.UUID | None = None


@dataclass
class AppointmentTurnResult:
    next_step: AppointmentStep
    reply_text: str
    persisted: AppointmentPersistedChanges


@dataclass
class AppointmentSummary:
    id: uuid.UUID
    doctor_name: str
    starts_at: datetime


@dataclass
class SlotCandidate:
    id: uuid.UUID
    doctor_name: str
    starts_at: datetime


T = TypeVar("T", bound=BaseModel)


class AnthropicAppointmentExtractor:
    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def extract_intent(self, text: str) -> IntentExtraction:
        return await self._parse(
            IntentExtraction,
            'Classify the patient\'s message as wanting to "book", "reschedule", or '
            '"cancel" an appointment, or "unclear" if none of those clearly apply.',
            text,
        )

    async def extract_datetime(self, text: str, reference_now: datetime) -> DateTimeExtraction:
        return await self._parse(
            DateTimeExtraction,
            f"The current date and time is {reference_now.isoformat()} (UTC). Extract "
            "the date and time the patient wants for their appointment, resolving any "
            "relative phrases (e.g. 'next Tuesday', 'tomorrow afternoon') against that "
            "current time. Respond with an ISO 8601 datetime in UTC. If no date or time "
            "is clearly stated, set iso_datetime to null.",
            text,
        )

    async def extract_selection(self, text: str, option_count: int) -> SelectionExtraction:
        return await self._parse(
            SelectionExtraction,
            f"The patient was shown {option_count} numbered options and asked to pick "
            "one. Extract which option number they chose. If unclear, set "
            "option_number to null.",
            text,
        )

    async def extract_confirmation(self, text: str) -> ConfirmationExtraction:
        return await self._parse(
            ConfirmationExtraction,
            'Classify the patient\'s reply to a yes/no confirmation question as '
            '"affirmative", "decline", or "unclear".',
            text,
        )

    async def _parse(self, output_format: type[T], instruction: str, text: str) -> T:
        try:
            response = await self._client.with_options(
                timeout=EXTRACTION_TIMEOUT_SECONDS
            ).messages.parse(
                model=APPOINTMENT_MODEL,
                max_tokens=256,
                messages=[
                    {
                        "role": "user",
                        "content": f"{instruction}\n\nPatient message: {text}",
                    }
                ],
                output_format=output_format,
            )
        except anthropic.APIError as exc:
            raise ExtractionFailure from exc

        parsed = response.parsed_output
        if parsed is None:
            raise ExtractionFailure
        return parsed


def _format_dt(dt: datetime, tz_name: str) -> str:
    localized = dt.astimezone(ZoneInfo(tz_name))
    return localized.strftime("%A, %B %d at %I:%M %p %Z")


def _format_slot_options(candidates: list[SlotCandidate], tz_name: str) -> str:
    lines = [
        f"{i}. {c.doctor_name} — {_format_dt(c.starts_at, tz_name)}"
        for i, c in enumerate(candidates, start=1)
    ]
    return "Here are the closest available slots:\n" + "\n".join(lines) + "\nReply with a number to book."


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def fetch_upcoming_appointments(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID
) -> list[AppointmentSummary]:
    rows = await conn.fetch(
        """
        SELECT a.id, d.name AS doctor_name, s.starts_at
        FROM appointments a
        JOIN availability_slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.clinic_id = $1 AND a.patient_id = $2 AND a.status = 'booked'
            AND s.starts_at > now()
        ORDER BY s.starts_at
        """,
        clinic_id,
        patient_id,
    )
    return [
        AppointmentSummary(id=row["id"], doctor_name=row["doctor_name"], starts_at=row["starts_at"])
        for row in rows
    ]


async def fetch_appointment_summary(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID, appointment_id: uuid.UUID
) -> AppointmentSummary | None:
    row = await conn.fetchrow(
        """
        SELECT a.id, d.name AS doctor_name, s.starts_at
        FROM appointments a
        JOIN availability_slots s ON s.id = a.slot_id
        JOIN doctors d ON d.id = a.doctor_id
        WHERE a.clinic_id = $1 AND a.patient_id = $2 AND a.id = $3
        """,
        clinic_id,
        patient_id,
        appointment_id,
    )
    if row is None:
        return None
    return AppointmentSummary(id=row["id"], doctor_name=row["doctor_name"], starts_at=row["starts_at"])


async def fetch_patient_department_id(
    conn: asyncpg.Connection, patient_id: uuid.UUID
) -> uuid.UUID | None:
    return await conn.fetchval("SELECT department_id FROM patients WHERE id = $1", patient_id)


async def fetch_clinic_timezone(conn: asyncpg.Connection, clinic_id: uuid.UUID) -> str:
    tz_name: str = await conn.fetchval("SELECT timezone FROM clinics WHERE id = $1", clinic_id)
    return tz_name


async def find_nearest_available_slots(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    department_id: uuid.UUID,
    target: datetime,
    limit: int,
) -> list[SlotCandidate]:
    rows = await conn.fetch(
        """
        SELECT s.id, d.name AS doctor_name, s.starts_at
        FROM availability_slots s
        JOIN doctors d ON d.id = s.doctor_id
        WHERE s.clinic_id = $1 AND d.department_id = $2 AND s.status = 'available'
            AND s.starts_at > now()
        ORDER BY ABS(EXTRACT(EPOCH FROM (s.starts_at - $3::timestamptz)))
        LIMIT $4
        """,
        clinic_id,
        department_id,
        target,
        limit,
    )
    return [
        SlotCandidate(id=row["id"], doctor_name=row["doctor_name"], starts_at=row["starts_at"])
        for row in rows
    ]


async def book_slot(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID, slot_id: uuid.UUID
) -> uuid.UUID | None:
    row = await conn.fetchrow(
        "SELECT status, doctor_id FROM availability_slots WHERE id = $1 AND clinic_id = $2 FOR UPDATE",
        slot_id,
        clinic_id,
    )
    if row is None or row["status"] != "available":
        return None

    await conn.execute("UPDATE availability_slots SET status = 'booked' WHERE id = $1", slot_id)
    appointment_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO appointments (clinic_id, patient_id, doctor_id, slot_id, status)
        VALUES ($1, $2, $3, $4, 'booked')
        RETURNING id
        """,
        clinic_id,
        patient_id,
        row["doctor_id"],
        slot_id,
    )
    return appointment_id


async def cancel_appointment(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, appointment_id: uuid.UUID
) -> bool:
    slot_id: uuid.UUID | None = await conn.fetchval(
        """
        UPDATE appointments SET status = 'cancelled', updated_at = now()
        WHERE id = $1 AND clinic_id = $2 AND status = 'booked'
        RETURNING slot_id
        """,
        appointment_id,
        clinic_id,
    )
    if slot_id is None:
        return False
    await conn.execute("UPDATE availability_slots SET status = 'available' WHERE id = $1", slot_id)
    return True


async def reschedule_appointment(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    old_appointment_id: uuid.UUID,
    new_slot_id: uuid.UUID,
) -> uuid.UUID | None:
    new_appointment_id = await book_slot(conn, clinic_id, patient_id, new_slot_id)
    if new_appointment_id is None:
        return None
    await cancel_appointment(conn, clinic_id, old_appointment_id)
    return new_appointment_id


def _state_to_json(state: AppointmentState) -> str:
    return json.dumps(
        {
            "step": state.step.value,
            "intent": state.intent,
            "target_appointment_id": (
                str(state.target_appointment_id) if state.target_appointment_id else None
            ),
            "candidate_ids": [str(candidate_id) for candidate_id in state.candidate_ids],
            "unclear_count": state.unclear_count,
        }
    )


def _state_from_json(raw: str) -> AppointmentState:
    data = json.loads(raw)
    return AppointmentState(
        step=AppointmentStep(data["step"]),
        intent=data["intent"],
        target_appointment_id=(
            uuid.UUID(data["target_appointment_id"]) if data["target_appointment_id"] else None
        ),
        candidate_ids=[uuid.UUID(candidate_id) for candidate_id in data["candidate_ids"]],
        unclear_count=data["unclear_count"],
    )


async def save_appointment_state(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID, state: AppointmentState
) -> None:
    await conn.execute(
        """
        UPDATE conversation_state
        SET state = $1::jsonb, updated_at = now()
        WHERE clinic_id = $2 AND patient_id = $3 AND agent = 'appointment'
        """,
        _state_to_json(state),
        clinic_id,
        patient_id,
    )


async def load_appointment_state(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID
) -> AppointmentState:
    initial_state = AppointmentState(
        step=AppointmentStep.AWAITING_INTENT,
        intent=None,
        target_appointment_id=None,
        candidate_ids=[],
        unclear_count=0,
    )
    await conn.fetchval(
        """
        INSERT INTO conversation_state (clinic_id, patient_id, agent, state)
        VALUES ($1, $2, 'appointment', $3::jsonb)
        ON CONFLICT (clinic_id, patient_id, agent) DO NOTHING
        RETURNING id
        """,
        clinic_id,
        patient_id,
        _state_to_json(initial_state),
    )
    row = await conn.fetchrow(
        """
        SELECT state FROM conversation_state
        WHERE clinic_id = $1 AND patient_id = $2 AND agent = 'appointment'
        FOR UPDATE
        """,
        clinic_id,
        patient_id,
    )
    assert row is not None
    return _state_from_json(row["state"])


async def _handle_unclear(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    normal_reply: str,
    bounded_reply: str,
) -> AppointmentTurnResult:
    unclear_count = min(state.unclear_count + 1, MAX_UNCLEAR_ATTEMPTS)
    next_state = AppointmentState(
        step=state.step,
        intent=state.intent,
        target_appointment_id=state.target_appointment_id,
        candidate_ids=state.candidate_ids,
        unclear_count=unclear_count,
    )
    await save_appointment_state(conn, clinic_id, patient_id, next_state)
    reply = bounded_reply if unclear_count >= MAX_UNCLEAR_ATTEMPTS else normal_reply
    return AppointmentTurnResult(
        next_step=state.step, reply_text=reply, persisted=AppointmentPersistedChanges()
    )


def _reset_state() -> AppointmentState:
    return AppointmentState(
        step=AppointmentStep.AWAITING_INTENT,
        intent=None,
        target_appointment_id=None,
        candidate_ids=[],
        unclear_count=0,
    )


async def _handle_awaiting_intent(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
    tz_name: str,
) -> AppointmentTurnResult:
    try:
        extraction = await extractor.extract_intent(message_text)
    except ExtractionFailure:
        return AppointmentTurnResult(state.step, EXTRACTION_FAILURE_REPLY, AppointmentPersistedChanges())

    if extraction.intent == "unclear":
        return await _handle_unclear(
            conn,
            clinic_id,
            patient_id,
            state,
            "Would you like to book, reschedule, or cancel an appointment?",
            "I still couldn't tell what you'd like to do — please reply with "
            "'book', 'reschedule', or 'cancel'.",
        )

    if extraction.intent == "book":
        next_state = AppointmentState(
            step=AppointmentStep.AWAITING_DATETIME,
            intent="book",
            target_appointment_id=None,
            candidate_ids=[],
            unclear_count=0,
        )
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "What date and time would you like to come in?",
            AppointmentPersistedChanges(),
        )

    appointments = await fetch_upcoming_appointments(conn, clinic_id, patient_id)
    if not appointments:
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "You don't have any upcoming appointments.",
            AppointmentPersistedChanges(),
        )

    if len(appointments) == 1:
        target = appointments[0]
        if extraction.intent == "cancel":
            next_state = AppointmentState(
                step=AppointmentStep.AWAITING_CANCEL_CONFIRMATION,
                intent="cancel",
                target_appointment_id=target.id,
                candidate_ids=[],
                unclear_count=0,
            )
            await save_appointment_state(conn, clinic_id, patient_id, next_state)
            return AppointmentTurnResult(
                next_state.step,
                f"Cancel your appointment with {target.doctor_name} on "
                f"{_format_dt(target.starts_at, tz_name)}? Reply YES or NO.",
                AppointmentPersistedChanges(),
            )
        next_state = AppointmentState(
            step=AppointmentStep.AWAITING_DATETIME,
            intent="reschedule",
            target_appointment_id=target.id,
            candidate_ids=[],
            unclear_count=0,
        )
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step, "What new date and time would you like?", AppointmentPersistedChanges()
        )

    next_state = AppointmentState(
        step=AppointmentStep.AWAITING_APPOINTMENT_SELECTION,
        intent=extraction.intent,
        target_appointment_id=None,
        candidate_ids=[appointment.id for appointment in appointments],
        unclear_count=0,
    )
    await save_appointment_state(conn, clinic_id, patient_id, next_state)
    lines = [
        f"{i}. {a.doctor_name} — {_format_dt(a.starts_at, tz_name)}"
        for i, a in enumerate(appointments, start=1)
    ]
    return AppointmentTurnResult(
        next_state.step,
        "You have multiple upcoming appointments:\n"
        + "\n".join(lines)
        + "\nReply with a number.",
        AppointmentPersistedChanges(),
    )


async def _handle_appointment_selection(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
    tz_name: str,
) -> AppointmentTurnResult:
    try:
        extraction = await extractor.extract_selection(message_text, len(state.candidate_ids))
    except ExtractionFailure:
        return AppointmentTurnResult(state.step, EXTRACTION_FAILURE_REPLY, AppointmentPersistedChanges())

    index = extraction.option_number
    if index is None or not (1 <= index <= len(state.candidate_ids)):
        return await _handle_unclear(
            conn,
            clinic_id,
            patient_id,
            state,
            "Please reply with the number of the appointment you mean.",
            "I still couldn't tell which appointment you mean — a staff member "
            "can help you directly.",
        )

    target_id = state.candidate_ids[index - 1]
    if state.intent == "cancel":
        appointment = await fetch_appointment_summary(conn, clinic_id, patient_id, target_id)
        if appointment is None:
            next_state = _reset_state()
            await save_appointment_state(conn, clinic_id, patient_id, next_state)
            return AppointmentTurnResult(
                next_state.step,
                "Something went wrong finding that appointment — please try again.",
                AppointmentPersistedChanges(),
            )
        next_state = AppointmentState(
            step=AppointmentStep.AWAITING_CANCEL_CONFIRMATION,
            intent="cancel",
            target_appointment_id=target_id,
            candidate_ids=[],
            unclear_count=0,
        )
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            f"Cancel your appointment with {appointment.doctor_name} on "
            f"{_format_dt(appointment.starts_at, tz_name)}? Reply YES or NO.",
            AppointmentPersistedChanges(),
        )

    next_state = AppointmentState(
        step=AppointmentStep.AWAITING_DATETIME,
        intent="reschedule",
        target_appointment_id=target_id,
        candidate_ids=[],
        unclear_count=0,
    )
    await save_appointment_state(conn, clinic_id, patient_id, next_state)
    return AppointmentTurnResult(
        next_state.step, "What new date and time would you like?", AppointmentPersistedChanges()
    )


async def _handle_datetime(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
    tz_name: str,
) -> AppointmentTurnResult:
    try:
        extraction = await extractor.extract_datetime(message_text, datetime.now(timezone.utc))
    except ExtractionFailure:
        return AppointmentTurnResult(state.step, EXTRACTION_FAILURE_REPLY, AppointmentPersistedChanges())

    target_dt = _parse_iso_datetime(extraction.iso_datetime)
    if target_dt is None or target_dt < datetime.now(timezone.utc):
        return await _handle_unclear(
            conn,
            clinic_id,
            patient_id,
            state,
            "Sorry, I couldn't understand that date/time — could you try again "
            "(e.g. 'next Tuesday at 3pm')?",
            "I still couldn't understand the date/time — a staff member can help "
            "you directly.",
        )

    department_id = await fetch_patient_department_id(conn, patient_id)
    if department_id is None:
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "Something went wrong finding your department — please contact the clinic directly.",
            AppointmentPersistedChanges(),
        )

    candidates = await find_nearest_available_slots(
        conn, clinic_id, department_id, target_dt, SLOT_SEARCH_CANDIDATE_COUNT
    )
    if not candidates:
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "Sorry, there are no available slots for your department right now.",
            AppointmentPersistedChanges(),
        )

    next_state = AppointmentState(
        step=AppointmentStep.AWAITING_SLOT_SELECTION,
        intent=state.intent,
        target_appointment_id=state.target_appointment_id,
        candidate_ids=[candidate.id for candidate in candidates],
        unclear_count=0,
    )
    await save_appointment_state(conn, clinic_id, patient_id, next_state)
    return AppointmentTurnResult(
        next_state.step, _format_slot_options(candidates, tz_name), AppointmentPersistedChanges()
    )


async def _handle_slot_selection(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
) -> AppointmentTurnResult:
    try:
        extraction = await extractor.extract_selection(message_text, len(state.candidate_ids))
    except ExtractionFailure:
        return AppointmentTurnResult(state.step, EXTRACTION_FAILURE_REPLY, AppointmentPersistedChanges())

    index = extraction.option_number
    if index is None or not (1 <= index <= len(state.candidate_ids)):
        return await _handle_unclear(
            conn,
            clinic_id,
            patient_id,
            state,
            "Please reply with the number of the slot you'd like.",
            "I still couldn't tell which slot you mean — a staff member can help you directly.",
        )

    slot_id = state.candidate_ids[index - 1]

    if state.intent == "reschedule":
        assert state.target_appointment_id is not None
        new_appointment_id = await reschedule_appointment(
            conn, clinic_id, patient_id, state.target_appointment_id, slot_id
        )
        if new_appointment_id is None:
            return AppointmentTurnResult(
                next_step=state.step,
                reply_text="Sorry, that slot was just taken — please pick another from the list.",
                persisted=AppointmentPersistedChanges(),
            )
        old_appointment_id = state.target_appointment_id
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "Your appointment has been rescheduled. Anything else?",
            AppointmentPersistedChanges(
                rescheduled_from_appointment_id=old_appointment_id,
                rescheduled_to_appointment_id=new_appointment_id,
            ),
        )

    appointment_id = await book_slot(conn, clinic_id, patient_id, slot_id)
    if appointment_id is None:
        return AppointmentTurnResult(
            next_step=state.step,
            reply_text="Sorry, that slot was just taken — please pick another from the list.",
            persisted=AppointmentPersistedChanges(),
        )
    next_state = _reset_state()
    await save_appointment_state(conn, clinic_id, patient_id, next_state)
    return AppointmentTurnResult(
        next_state.step,
        "Your appointment is booked! Anything else?",
        AppointmentPersistedChanges(booked_appointment_id=appointment_id),
    )


async def _handle_cancel_confirmation(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
) -> AppointmentTurnResult:
    try:
        extraction = await extractor.extract_confirmation(message_text)
    except ExtractionFailure:
        return AppointmentTurnResult(state.step, EXTRACTION_FAILURE_REPLY, AppointmentPersistedChanges())

    if extraction.decision == "affirmative":
        assert state.target_appointment_id is not None
        cancelled = await cancel_appointment(conn, clinic_id, state.target_appointment_id)
        cancelled_id = state.target_appointment_id
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        if not cancelled:
            return AppointmentTurnResult(
                next_state.step,
                "That appointment is no longer active — nothing to cancel. Anything else?",
                AppointmentPersistedChanges(),
            )
        return AppointmentTurnResult(
            next_state.step,
            "Your appointment has been cancelled. Anything else?",
            AppointmentPersistedChanges(cancelled_appointment_id=cancelled_id),
        )

    if extraction.decision == "decline":
        next_state = _reset_state()
        await save_appointment_state(conn, clinic_id, patient_id, next_state)
        return AppointmentTurnResult(
            next_state.step,
            "Okay, your appointment is still booked. Anything else?",
            AppointmentPersistedChanges(),
        )

    return await _handle_unclear(
        conn,
        clinic_id,
        patient_id,
        state,
        "Please reply YES or NO — should I cancel this appointment?",
        "I still couldn't tell — a staff member can help you directly.",
    )


async def advance_appointment(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: AppointmentState,
    message_text: str,
    extractor: AppointmentExtractor,
) -> AppointmentTurnResult:
    if state.step == AppointmentStep.AWAITING_INTENT:
        tz_name = await fetch_clinic_timezone(conn, clinic_id)
        return await _handle_awaiting_intent(
            conn, clinic_id, patient_id, state, message_text, extractor, tz_name
        )
    if state.step == AppointmentStep.AWAITING_APPOINTMENT_SELECTION:
        tz_name = await fetch_clinic_timezone(conn, clinic_id)
        return await _handle_appointment_selection(
            conn, clinic_id, patient_id, state, message_text, extractor, tz_name
        )
    if state.step == AppointmentStep.AWAITING_DATETIME:
        tz_name = await fetch_clinic_timezone(conn, clinic_id)
        return await _handle_datetime(
            conn, clinic_id, patient_id, state, message_text, extractor, tz_name
        )
    if state.step == AppointmentStep.AWAITING_SLOT_SELECTION:
        return await _handle_slot_selection(
            conn, clinic_id, patient_id, state, message_text, extractor
        )
    return await _handle_cancel_confirmation(
        conn, clinic_id, patient_id, state, message_text, extractor
    )
