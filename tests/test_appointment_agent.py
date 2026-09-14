import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio
from appointment_agent import (
    AppointmentStep,
    ConfirmationExtraction,
    DateTimeExtraction,
    ExtractionFailure,
    IntentExtraction,
    SelectionExtraction,
    advance_appointment,
    book_slot,
    load_appointment_state,
)
from main import load_db_config


@dataclass
class FakeAppointmentExtractor:
    intent_result: IntentExtraction | None = None
    datetime_result: DateTimeExtraction | None = None
    selection_result: SelectionExtraction | None = None
    confirmation_result: ConfirmationExtraction | None = None
    fail: bool = False

    async def extract_intent(self, text: str) -> IntentExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.intent_result is not None
        return self.intent_result

    async def extract_datetime(self, text: str, reference_now: datetime) -> DateTimeExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.datetime_result is not None
        return self.datetime_result

    async def extract_selection(self, text: str, option_count: int) -> SelectionExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.selection_result is not None
        return self.selection_result

    async def extract_confirmation(self, text: str) -> ConfirmationExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.confirmation_result is not None
        return self.confirmation_result


@pytest_asyncio.fixture
async def scoped_conn() -> AsyncIterator[tuple[asyncpg.Connection, uuid.UUID]]:
    db_config = load_db_config()
    conn = await asyncpg.connect(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
    )
    clinic_id = await conn.fetchval("SELECT resolve_clinic_by_phone($1)", "sunrise-main")
    tx = conn.transaction()
    await tx.start()
    await conn.execute("SELECT set_config('app.current_clinic_id', $1, true)", str(clinic_id))
    try:
        yield conn, clinic_id
    finally:
        await tx.rollback()
        await conn.close()


async def create_patient(conn: asyncpg.Connection, clinic_id: uuid.UUID) -> uuid.UUID:
    department_id = await conn.fetchval(
        "SELECT id FROM departments WHERE clinic_id = $1 AND name = 'Cardiology'", clinic_id
    )
    patient_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO patients (clinic_id, whatsapp_id, department_id) VALUES ($1, $2, $3) RETURNING id",
        clinic_id,
        f"+1{uuid.uuid4().int % 10**10}",
        department_id,
    )
    return patient_id


async def create_available_slot(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, days_ahead: int
) -> tuple[uuid.UUID, uuid.UUID]:
    doctor_id: uuid.UUID = await conn.fetchval(
        """
        SELECT d.id FROM doctors d
        JOIN departments dep ON dep.id = d.department_id
        WHERE d.clinic_id = $1 AND dep.name = 'Cardiology'
        LIMIT 1
        """,
        clinic_id,
    )
    slot_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO availability_slots (clinic_id, doctor_id, starts_at, ends_at, status)
        VALUES (
            $1, $2,
            now() + make_interval(days => $3), now() + make_interval(days => $3, mins => 30),
            'available'
        )
        RETURNING id
        """,
        clinic_id,
        doctor_id,
        days_ahead,
    )
    return slot_id, doctor_id


async def create_booked_appointment(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID, days_ahead: int
) -> tuple[uuid.UUID, uuid.UUID]:
    slot_id, doctor_id = await create_available_slot(conn, clinic_id, days_ahead)
    await conn.execute("UPDATE availability_slots SET status = 'booked' WHERE id = $1", slot_id)
    appointment_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO appointments (clinic_id, patient_id, doctor_id, slot_id, status)
        VALUES ($1, $2, $3, $4, 'booked')
        RETURNING id
        """,
        clinic_id,
        patient_id,
        doctor_id,
        slot_id,
    )
    return appointment_id, slot_id


@pytest.mark.asyncio
async def test_full_book_flow_completes(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    slot_id, _ = await create_available_slot(conn, clinic_id, days_ahead=45)

    state = await load_appointment_state(conn, clinic_id, patient_id)
    assert state.step == AppointmentStep.AWAITING_INTENT

    extractor = FakeAppointmentExtractor(intent_result=IntentExtraction(intent="book"))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "I want to book", extractor)
    assert result.next_step == AppointmentStep.AWAITING_DATETIME

    target_iso = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(datetime_result=DateTimeExtraction(iso_datetime=target_iso))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "in 45 days", extractor)
    assert result.next_step == AppointmentStep.AWAITING_SLOT_SELECTION
    assert "1." in result.reply_text

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(selection_result=SelectionExtraction(option_number=1))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "1", extractor)
    assert result.next_step == AppointmentStep.AWAITING_INTENT
    assert result.persisted.booked_appointment_id is not None

    slot_status = await conn.fetchval("SELECT status FROM availability_slots WHERE id = $1", slot_id)
    assert slot_status == "booked"
    booked_count = await conn.fetchval(
        "SELECT count(*) FROM appointments WHERE patient_id = $1 AND status = 'booked'", patient_id
    )
    assert booked_count == 1


@pytest.mark.asyncio
async def test_cancel_single_appointment(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    appointment_id, slot_id = await create_booked_appointment(conn, clinic_id, patient_id, days_ahead=10)

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(intent_result=IntentExtraction(intent="cancel"))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "cancel please", extractor)
    assert result.next_step == AppointmentStep.AWAITING_CANCEL_CONFIRMATION

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(
        confirmation_result=ConfirmationExtraction(decision="affirmative")
    )
    result = await advance_appointment(conn, clinic_id, patient_id, state, "yes", extractor)
    assert result.next_step == AppointmentStep.AWAITING_INTENT
    assert result.persisted.cancelled_appointment_id == appointment_id

    appt_status = await conn.fetchval("SELECT status FROM appointments WHERE id = $1", appointment_id)
    assert appt_status == "cancelled"
    slot_status = await conn.fetchval("SELECT status FROM availability_slots WHERE id = $1", slot_id)
    assert slot_status == "available"


@pytest.mark.asyncio
async def test_cancel_multiple_appointments_requires_disambiguation(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    first_id, _ = await create_booked_appointment(conn, clinic_id, patient_id, days_ahead=10)
    second_id, _ = await create_booked_appointment(conn, clinic_id, patient_id, days_ahead=20)

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(intent_result=IntentExtraction(intent="cancel"))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "cancel", extractor)
    assert result.next_step == AppointmentStep.AWAITING_APPOINTMENT_SELECTION

    state = await load_appointment_state(conn, clinic_id, patient_id)
    assert state.candidate_ids == [first_id, second_id]

    extractor = FakeAppointmentExtractor(selection_result=SelectionExtraction(option_number=1))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "1", extractor)
    assert result.next_step == AppointmentStep.AWAITING_CANCEL_CONFIRMATION

    state = await load_appointment_state(conn, clinic_id, patient_id)
    assert state.target_appointment_id == first_id


@pytest.mark.asyncio
async def test_reschedule_flow(scoped_conn: tuple[asyncpg.Connection, uuid.UUID]) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    old_appointment_id, old_slot_id = await create_booked_appointment(
        conn, clinic_id, patient_id, days_ahead=10
    )
    new_slot_id, _ = await create_available_slot(conn, clinic_id, days_ahead=50)

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(intent_result=IntentExtraction(intent="reschedule"))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "reschedule", extractor)
    assert result.next_step == AppointmentStep.AWAITING_DATETIME

    target_iso = (datetime.now(timezone.utc) + timedelta(days=50)).isoformat()
    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(datetime_result=DateTimeExtraction(iso_datetime=target_iso))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "in 50 days", extractor)
    assert result.next_step == AppointmentStep.AWAITING_SLOT_SELECTION

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(selection_result=SelectionExtraction(option_number=1))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "1", extractor)
    assert result.next_step == AppointmentStep.AWAITING_INTENT
    assert result.persisted.rescheduled_from_appointment_id == old_appointment_id
    assert result.persisted.rescheduled_to_appointment_id is not None

    old_status = await conn.fetchval("SELECT status FROM appointments WHERE id = $1", old_appointment_id)
    assert old_status == "cancelled"
    old_slot_status = await conn.fetchval(
        "SELECT status FROM availability_slots WHERE id = $1", old_slot_id
    )
    assert old_slot_status == "available"
    new_slot_status = await conn.fetchval(
        "SELECT status FROM availability_slots WHERE id = $1", new_slot_id
    )
    assert new_slot_status == "booked"


@pytest.mark.asyncio
async def test_unclear_datetime_bounded_reprompt(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)

    from appointment_agent import MAX_UNCLEAR_ATTEMPTS, AppointmentState, save_appointment_state

    await load_appointment_state(conn, clinic_id, patient_id)
    state = AppointmentState(
        step=AppointmentStep.AWAITING_DATETIME,
        intent="book",
        target_appointment_id=None,
        candidate_ids=[],
        unclear_count=0,
    )
    await save_appointment_state(conn, clinic_id, patient_id, state)

    extractor = FakeAppointmentExtractor(datetime_result=DateTimeExtraction(iso_datetime=None))
    for _ in range(MAX_UNCLEAR_ATTEMPTS + 2):
        result = await advance_appointment(
            conn, clinic_id, patient_id, state, "sometime maybe", extractor
        )
        assert result.next_step == AppointmentStep.AWAITING_DATETIME
        state = await load_appointment_state(conn, clinic_id, patient_id)

    assert state.unclear_count == MAX_UNCLEAR_ATTEMPTS
    assert "staff member" in result.reply_text


@pytest.mark.asyncio
async def test_cancel_completed_appointment_is_safe_noop(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    appointment_id, slot_id = await create_booked_appointment(conn, clinic_id, patient_id, days_ahead=10)
    await conn.execute("UPDATE appointments SET status = 'completed' WHERE id = $1", appointment_id)

    from appointment_agent import cancel_appointment

    cancelled = await cancel_appointment(conn, clinic_id, appointment_id)
    assert cancelled is False

    status = await conn.fetchval("SELECT status FROM appointments WHERE id = $1", appointment_id)
    assert status == "completed"
    slot_status = await conn.fetchval("SELECT status FROM availability_slots WHERE id = $1", slot_id)
    assert slot_status == "booked"


@pytest.mark.asyncio
async def test_race_loss_does_not_consume_unclear_budget(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    slot_id, _ = await create_available_slot(conn, clinic_id, days_ahead=60)

    from appointment_agent import AppointmentState, save_appointment_state

    state = AppointmentState(
        step=AppointmentStep.AWAITING_SLOT_SELECTION,
        intent="book",
        target_appointment_id=None,
        candidate_ids=[slot_id],
        unclear_count=0,
    )
    await save_appointment_state(conn, clinic_id, patient_id, state)

    await conn.execute("UPDATE availability_slots SET status = 'booked' WHERE id = $1", slot_id)

    extractor = FakeAppointmentExtractor(selection_result=SelectionExtraction(option_number=1))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "1", extractor)

    assert result.next_step == AppointmentStep.AWAITING_SLOT_SELECTION
    assert "just taken" in result.reply_text
    assert "staff member" not in result.reply_text

    reloaded = await load_appointment_state(conn, clinic_id, patient_id)
    assert reloaded.unclear_count == 0


@pytest.mark.asyncio
async def test_fetch_appointment_summary_returns_none_for_wrong_patient(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_a = await create_patient(conn, clinic_id)
    patient_b = await create_patient(conn, clinic_id)
    appointment_id, _ = await create_booked_appointment(conn, clinic_id, patient_a, days_ahead=10)

    from appointment_agent import fetch_appointment_summary

    wrong_patient_result = await fetch_appointment_summary(conn, clinic_id, patient_b, appointment_id)
    assert wrong_patient_result is None

    correct_result = await fetch_appointment_summary(conn, clinic_id, patient_a, appointment_id)
    assert correct_result is not None


@pytest.mark.asyncio
async def test_slot_times_use_clinic_timezone(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    await create_available_slot(conn, clinic_id, days_ahead=45)

    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(intent_result=IntentExtraction(intent="book"))
    await advance_appointment(conn, clinic_id, patient_id, state, "book", extractor)

    target_iso = (datetime.now(timezone.utc) + timedelta(days=45)).isoformat()
    state = await load_appointment_state(conn, clinic_id, patient_id)
    extractor = FakeAppointmentExtractor(datetime_result=DateTimeExtraction(iso_datetime=target_iso))
    result = await advance_appointment(conn, clinic_id, patient_id, state, "in 45 days", extractor)

    assert "IST" in result.reply_text
    assert "UTC" not in result.reply_text


@pytest.mark.asyncio
async def test_concurrent_booking_only_one_succeeds() -> None:
    db_config = load_db_config()

    async def connect() -> asyncpg.Connection:
        return await asyncpg.connect(
            host=db_config.host,
            port=db_config.port,
            database=db_config.database,
            user="threshold_api_user",
            password=db_config.password,
        )

    setup_conn = await connect()
    clinic_id = await setup_conn.fetchval("SELECT resolve_clinic_by_phone($1)", "sunrise-main")
    await setup_conn.execute(
        "SELECT set_config('app.current_clinic_id', $1, false)", str(clinic_id)
    )
    patient_a_id = await setup_conn.fetchval(
        "INSERT INTO patients (clinic_id, whatsapp_id) VALUES ($1, $2) RETURNING id",
        clinic_id,
        f"+1{uuid.uuid4().int % 10**10}",
    )
    patient_b_id = await setup_conn.fetchval(
        "INSERT INTO patients (clinic_id, whatsapp_id) VALUES ($1, $2) RETURNING id",
        clinic_id,
        f"+1{uuid.uuid4().int % 10**10}",
    )
    slot_id, _ = await create_available_slot(setup_conn, clinic_id, days_ahead=90)
    await setup_conn.close()

    conn_a = await connect()
    conn_b = await connect()
    await conn_a.execute("SELECT set_config('app.current_clinic_id', $1, false)", str(clinic_id))
    await conn_b.execute("SELECT set_config('app.current_clinic_id', $1, false)", str(clinic_id))

    tx_a = conn_a.transaction()
    await tx_a.start()
    tx_b = conn_b.transaction()
    await tx_b.start()

    try:
        result_a = await book_slot(conn_a, clinic_id, patient_a_id, slot_id)
        assert result_a is not None

        book_b_task = asyncio.create_task(book_slot(conn_b, clinic_id, patient_b_id, slot_id))
        await asyncio.sleep(0.2)
        assert not book_b_task.done()

        await tx_a.commit()

        result_b = await book_b_task
        assert result_b is None
    finally:
        try:
            await tx_b.rollback()
        except asyncpg.InterfaceError:
            pass
        await conn_a.close()
        await conn_b.close()
