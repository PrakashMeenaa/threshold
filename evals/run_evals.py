import asyncio
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

API_DIR = Path(__file__).resolve().parent.parent / "apps" / "api"
sys.path.insert(0, str(API_DIR))

from appointment_agent import (  # noqa: E402
    AppointmentExtractor,
    AppointmentPersistedChanges,
    AppointmentState,
    AppointmentStep,
    AppointmentTurnResult,
    ConfirmationExtraction,
    DateTimeExtraction,
    IntentExtraction,
    SelectionExtraction,
    advance_appointment,
    load_appointment_state,
)
from appointment_agent import ExtractionFailure as AppointmentExtractionFailure  # noqa: E402
from main import CLINICS_DIR, load_db_config  # noqa: E402
from onboarding_agent import (  # noqa: E402
    ClinicRuntimeConfig,
    ConsentExtraction,
    DepartmentExtraction,
    LanguageExtraction,
    NameExtraction,
    OnboardingState,
    OnboardingStep,
    PersistedChanges,
    TurnResult,
    advance_onboarding,
    department_question,
    fetch_department_names,
    language_question,
    load_clinic_configs,
    load_onboarding_state,
    save_onboarding_state,
)
from onboarding_agent import ExtractionFailure as OnboardingExtractionFailure  # noqa: E402
from safety_gate import action_for_category, evaluate  # noqa: E402

CASES_FILE = Path(__file__).resolve().parent / "cases.json"


class MockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "name", "language", "consent", "department",
        "intent", "datetime", "selection", "confirmation", "fail",
    ]
    name: str | None = None
    language: str | None = None
    decision: Literal["affirmative", "decline", "unclear"] | None = None
    department: str | None = None
    intent: Literal["book", "reschedule", "cancel", "unclear"] | None = None
    days_ahead: float | None = None
    option_number: int | None = None


class ExpectedPersisted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_name: str | None = None
    patient_language: str | None = None
    consent_granted: bool | None = None
    department: str | None = None
    booked: bool | None = None
    cancelled: bool | None = None
    rescheduled: bool | None = None


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: Literal["onboarding", "appointment"]
    text: str
    mock: MockSpec
    expect_next_step: str
    expect_reply_contains: list[str] = Field(default_factory=list)
    expect_reply_equals: str | None = None
    expect_reply_template: Literal["consent_text", "language_question", "department_question"] | None = None
    expect_persisted: ExpectedPersisted | None = None
    verify_resume_before: bool = False
    mark_candidates_booked_before: bool = False


class OnboardingStateOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: str
    name: str | None = None
    language: str | None = None
    unclear_count: int = 0


class CaseSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset_patient_department: str | None = None
    slot_department: str | None = None
    available_slots_days_ahead: list[float] = Field(default_factory=list)
    booked_appointments_days_ahead: list[float] = Field(default_factory=list)
    onboarding_state: OnboardingStateOverride | None = None


class FinalAssertion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal[
        "onboarding_unclear_count_equals",
        "appointment_unclear_count_equals",
        "consent_granted_equals",
        "consent_row_absent",
        "patient_department_null",
        "booked_count_equals",
        "total_appointments_count_equals",
        "candidate_ids_count_equals",
    ]
    expected_bool: bool | None = None
    expected_int: int | None = None


class GateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    expected_category: str
    expected_action: Literal["escalated", "deflected"]


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str
    category: Literal["happy_path", "failure", "edge"]
    kind: Literal["conversation", "gate"]
    description: str
    clinic: str | None = None
    setup: CaseSetup = Field(default_factory=CaseSetup)
    turns: list[Turn] = Field(default_factory=list)
    gate: GateCheck | None = None
    final_assertions: list[FinalAssertion] = Field(default_factory=list)


@dataclass
class MockSlotExtractor:
    queue: list[MockSpec] = field(default_factory=list)

    def _next(self, expected_kind: str) -> MockSpec:
        spec = self.queue.pop(0)
        if spec.kind == "fail":
            raise OnboardingExtractionFailure
        if spec.kind != expected_kind:
            raise AssertionError(f"expected mock kind '{expected_kind}', got '{spec.kind}'")
        return spec

    async def extract_name(self, text: str) -> NameExtraction:
        spec = self._next("name")
        return NameExtraction(name=spec.name)

    async def extract_language(self, text: str, available_languages: list[str]) -> LanguageExtraction:
        spec = self._next("language")
        return LanguageExtraction(language=spec.language)

    async def extract_consent(self, text: str) -> ConsentExtraction:
        spec = self._next("consent")
        assert spec.decision is not None
        return ConsentExtraction(decision=spec.decision)

    async def extract_department(self, text: str, available_departments: list[str]) -> DepartmentExtraction:
        spec = self._next("department")
        return DepartmentExtraction(department=spec.department)


@dataclass
class MockAppointmentExtractor:
    queue: list[MockSpec] = field(default_factory=list)

    def _next(self, expected_kind: str) -> MockSpec:
        spec = self.queue.pop(0)
        if spec.kind == "fail":
            raise AppointmentExtractionFailure
        if spec.kind != expected_kind:
            raise AssertionError(f"expected mock kind '{expected_kind}', got '{spec.kind}'")
        return spec

    async def extract_intent(self, text: str) -> IntentExtraction:
        spec = self._next("intent")
        assert spec.intent is not None
        return IntentExtraction(intent=spec.intent)

    async def extract_datetime(self, text: str, reference_now: datetime) -> DateTimeExtraction:
        spec = self._next("datetime")
        assert spec.days_ahead is not None
        target = reference_now + timedelta(days=spec.days_ahead)
        return DateTimeExtraction(iso_datetime=target.isoformat())

    async def extract_selection(self, text: str, option_count: int) -> SelectionExtraction:
        spec = self._next("selection")
        return SelectionExtraction(option_number=spec.option_number)

    async def extract_confirmation(self, text: str) -> ConfirmationExtraction:
        spec = self._next("confirmation")
        assert spec.decision is not None
        return ConfirmationExtraction(decision=spec.decision)


@dataclass
class CaseOutcome:
    case_id: int
    name: str
    passed: bool
    failures: list[str]


async def _create_patient(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, department: str | None
) -> uuid.UUID:
    whatsapp_id = f"+1{uuid.uuid4().int % 10**10}"
    department_id: uuid.UUID | None = None
    if department is not None:
        department_id = await conn.fetchval(
            "SELECT id FROM departments WHERE clinic_id = $1 AND name = $2", clinic_id, department
        )
    patient_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO patients (clinic_id, whatsapp_id, department_id) VALUES ($1, $2, $3) RETURNING id",
        clinic_id,
        whatsapp_id,
        department_id,
    )
    return patient_id


async def _create_available_slot(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, department: str, days_ahead: float
) -> uuid.UUID:
    doctor_id: uuid.UUID = await conn.fetchval(
        """
        SELECT d.id FROM doctors d
        JOIN departments dep ON dep.id = d.department_id
        WHERE d.clinic_id = $1 AND dep.name = $2
        LIMIT 1
        """,
        clinic_id,
        department,
    )
    slot_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO availability_slots (clinic_id, doctor_id, starts_at, ends_at, status)
        VALUES (
            $1, $2,
            now() + make_interval(secs => $3), now() + make_interval(secs => $3) + interval '30 minutes',
            'available'
        )
        RETURNING id
        """,
        clinic_id,
        doctor_id,
        days_ahead * 86400,
    )
    return slot_id


async def _create_booked_appointment(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    department: str,
    days_ahead: float,
) -> uuid.UUID:
    slot_id = await _create_available_slot(conn, clinic_id, department, days_ahead)
    doctor_id: uuid.UUID = await conn.fetchval(
        "SELECT doctor_id FROM availability_slots WHERE id = $1", slot_id
    )
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
    return appointment_id


def _check_reply(turn: Turn, reply_text: str, clinic_config: ClinicRuntimeConfig, departments: list[str], failures: list[str]) -> None:
    if turn.expect_reply_equals is not None and reply_text != turn.expect_reply_equals:
        failures.append(f"reply mismatch: expected exactly {turn.expect_reply_equals!r}, got {reply_text!r}")
    if turn.expect_reply_template is not None:
        expected = {
            "consent_text": clinic_config.consent_text,
            "language_question": language_question(clinic_config.languages),
            "department_question": department_question(departments),
        }[turn.expect_reply_template]
        if reply_text != expected:
            failures.append(f"reply mismatch: expected template {turn.expect_reply_template!r} ({expected!r}), got {reply_text!r}")
    for substring in turn.expect_reply_contains:
        if substring not in reply_text:
            failures.append(f"reply missing expected substring {substring!r}: got {reply_text!r}")


def _check_persisted_onboarding(turn: Turn, persisted: PersistedChanges, failures: list[str]) -> None:
    expect = turn.expect_persisted
    if expect is None:
        return
    if expect.patient_name is not None and persisted.patient_name != expect.patient_name:
        failures.append(f"persisted.patient_name: expected {expect.patient_name!r}, got {persisted.patient_name!r}")
    if expect.patient_language is not None and persisted.patient_language != expect.patient_language:
        failures.append(f"persisted.patient_language: expected {expect.patient_language!r}, got {persisted.patient_language!r}")
    if expect.consent_granted is not None and persisted.consent_granted != expect.consent_granted:
        failures.append(f"persisted.consent_granted: expected {expect.consent_granted!r}, got {persisted.consent_granted!r}")
    if expect.department is not None and persisted.department != expect.department:
        failures.append(f"persisted.department: expected {expect.department!r}, got {persisted.department!r}")


def _check_persisted_appointment(turn: Turn, persisted: AppointmentPersistedChanges, failures: list[str]) -> None:
    expect = turn.expect_persisted
    if expect is None:
        return
    if expect.booked is not None and (persisted.booked_appointment_id is not None) != expect.booked:
        failures.append(f"persisted booked flag: expected {expect.booked!r}, got id={persisted.booked_appointment_id!r}")
    if expect.cancelled is not None and (persisted.cancelled_appointment_id is not None) != expect.cancelled:
        failures.append(f"persisted cancelled flag: expected {expect.cancelled!r}, got id={persisted.cancelled_appointment_id!r}")
    if expect.rescheduled is not None and (persisted.rescheduled_to_appointment_id is not None) != expect.rescheduled:
        failures.append(f"persisted rescheduled flag: expected {expect.rescheduled!r}, got id={persisted.rescheduled_to_appointment_id!r}")


async def _run_conversation_case(
    conn: asyncpg.Connection,
    case: Case,
    clinic_configs: dict[str, ClinicRuntimeConfig],
) -> list[str]:
    failures: list[str] = []
    assert case.clinic is not None
    clinic_id: uuid.UUID = await conn.fetchval("SELECT resolve_clinic_by_phone($1)", case.clinic)
    clinic_config = clinic_configs[case.clinic]

    patient_id = await _create_patient(conn, clinic_id, case.setup.preset_patient_department)

    if case.setup.slot_department is not None:
        for days_ahead in case.setup.available_slots_days_ahead:
            await _create_available_slot(conn, clinic_id, case.setup.slot_department, days_ahead)
        for days_ahead in case.setup.booked_appointments_days_ahead:
            await _create_booked_appointment(conn, clinic_id, patient_id, case.setup.slot_department, days_ahead)

    onboarding_state: OnboardingState = (await load_onboarding_state(conn, clinic_id, patient_id)).state
    if case.setup.onboarding_state is not None:
        override = case.setup.onboarding_state
        onboarding_state = OnboardingState(
            step=OnboardingStep(override.step),
            name=override.name,
            language=override.language,
            department=None,
            unclear_count=override.unclear_count,
        )
        await save_onboarding_state(conn, clinic_id, patient_id, onboarding_state)

    appointment_state: AppointmentState | None = None
    slot_extractor = MockSlotExtractor()
    appointment_extractor = MockAppointmentExtractor()

    for turn in case.turns:
        if turn.agent == "onboarding":
            if turn.verify_resume_before:
                reloaded = (await load_onboarding_state(conn, clinic_id, patient_id)).state
                if reloaded.step != onboarding_state.step or reloaded.name != onboarding_state.name:
                    failures.append(
                        f"resume check failed: expected step={onboarding_state.step!r} name={onboarding_state.name!r}, "
                        f"got step={reloaded.step!r} name={reloaded.name!r}"
                    )
                onboarding_state = reloaded

            slot_extractor.queue = [turn.mock]
            result: TurnResult = await advance_onboarding(
                conn, clinic_id, patient_id, onboarding_state, turn.text, clinic_config, slot_extractor
            )
            if result.next_step.value != turn.expect_next_step:
                failures.append(f"next_step: expected {turn.expect_next_step!r}, got {result.next_step.value!r}")
            departments = await fetch_department_names(conn, clinic_id)
            _check_reply(turn, result.reply_text, clinic_config, departments, failures)
            _check_persisted_onboarding(turn, result.persisted, failures)
            onboarding_state = (await load_onboarding_state(conn, clinic_id, patient_id)).state
        else:
            appointment_state = await load_appointment_state(conn, clinic_id, patient_id)

            if turn.mark_candidates_booked_before:
                await conn.execute(
                    "UPDATE availability_slots SET status = 'booked' WHERE id = ANY($1::uuid[])",
                    appointment_state.candidate_ids,
                )

            appointment_extractor.queue = [turn.mock]
            appt_result: AppointmentTurnResult = await advance_appointment(
                conn, clinic_id, patient_id, appointment_state, turn.text, appointment_extractor
            )
            if appt_result.next_step.value != turn.expect_next_step:
                failures.append(f"next_step: expected {turn.expect_next_step!r}, got {appt_result.next_step.value!r}")
            _check_reply(turn, appt_result.reply_text, clinic_config, [], failures)
            _check_persisted_appointment(turn, appt_result.persisted, failures)
            appointment_state = await load_appointment_state(conn, clinic_id, patient_id)

    for assertion in case.final_assertions:
        await _check_final_assertion(conn, clinic_id, patient_id, assertion, failures)

    return failures


async def _check_final_assertion(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    assertion: FinalAssertion,
    failures: list[str],
) -> None:
    if assertion.type == "onboarding_unclear_count_equals":
        state = (await load_onboarding_state(conn, clinic_id, patient_id)).state
        if state.unclear_count != assertion.expected_int:
            failures.append(f"onboarding_unclear_count: expected {assertion.expected_int}, got {state.unclear_count}")
    elif assertion.type == "appointment_unclear_count_equals":
        appt_state = await load_appointment_state(conn, clinic_id, patient_id)
        if appt_state.unclear_count != assertion.expected_int:
            failures.append(f"appointment_unclear_count: expected {assertion.expected_int}, got {appt_state.unclear_count}")
    elif assertion.type == "consent_granted_equals":
        granted = await conn.fetchval(
            """
            SELECT granted FROM consents WHERE clinic_id = $1 AND patient_id = $2
            ORDER BY granted_at DESC LIMIT 1
            """,
            clinic_id,
            patient_id,
        )
        if granted != assertion.expected_bool:
            failures.append(f"consent granted: expected {assertion.expected_bool}, got {granted}")
    elif assertion.type == "consent_row_absent":
        count = await conn.fetchval(
            "SELECT count(*) FROM consents WHERE clinic_id = $1 AND patient_id = $2", clinic_id, patient_id
        )
        if count != 0:
            failures.append(f"consent_row_absent: expected 0 rows, found {count}")
    elif assertion.type == "patient_department_null":
        department_id = await conn.fetchval("SELECT department_id FROM patients WHERE id = $1", patient_id)
        if department_id is not None:
            failures.append(f"patient_department_null: expected null, got {department_id}")
    elif assertion.type == "booked_count_equals":
        count = await conn.fetchval(
            "SELECT count(*) FROM appointments WHERE patient_id = $1 AND status = 'booked'", patient_id
        )
        if count != assertion.expected_int:
            failures.append(f"booked_count: expected {assertion.expected_int}, got {count}")
    elif assertion.type == "total_appointments_count_equals":
        count = await conn.fetchval("SELECT count(*) FROM appointments WHERE patient_id = $1", patient_id)
        if count != assertion.expected_int:
            failures.append(f"total_appointments_count: expected {assertion.expected_int}, got {count}")
    elif assertion.type == "candidate_ids_count_equals":
        appt_state = await load_appointment_state(conn, clinic_id, patient_id)
        if len(appt_state.candidate_ids) != assertion.expected_int:
            failures.append(f"candidate_ids_count: expected {assertion.expected_int}, got {len(appt_state.candidate_ids)}")


def _run_gate_case(case: Case) -> list[str]:
    failures: list[str] = []
    assert case.gate is not None
    result = evaluate(case.gate.text)
    if not result.triggered:
        failures.append("gate did not trigger")
        return failures
    if result.category != case.gate.expected_category:
        failures.append(f"category: expected {case.gate.expected_category!r}, got {result.category!r}")
    assert result.category is not None
    action = action_for_category(result.category)
    if action != case.gate.expected_action:
        failures.append(f"action: expected {case.gate.expected_action!r}, got {action!r}")
    return failures


def _format_exception(exc: Exception) -> str:
    return f"unhandled exception: {type(exc).__name__}: {exc}"


async def _run_case(
    pool: asyncpg.Pool,
    case: Case,
    clinic_configs: dict[str, ClinicRuntimeConfig],
) -> CaseOutcome:
    if case.kind == "gate":
        try:
            failures = _run_gate_case(case)
        except Exception as exc:
            failures = [_format_exception(exc)]
        return CaseOutcome(case_id=case.id, name=case.name, passed=not failures, failures=failures)

    conn = await pool.acquire()
    tx = conn.transaction()
    await tx.start()
    try:
        try:
            assert case.clinic is not None
            clinic_id: uuid.UUID = await conn.fetchval("SELECT resolve_clinic_by_phone($1)", case.clinic)
            await conn.execute("SELECT set_config('app.current_clinic_id', $1, true)", str(clinic_id))
            failures = await _run_conversation_case(conn, case, clinic_configs)
        except Exception as exc:
            failures = [_format_exception(exc)]
    finally:
        await tx.rollback()
        await pool.release(conn)

    return CaseOutcome(case_id=case.id, name=case.name, passed=not failures, failures=failures)


def _load_cases() -> list[Case]:
    raw = CASES_FILE.read_text(encoding="utf-8")
    return TypeAdapter(list[Case]).validate_json(raw)


def _print_report(outcomes: Sequence[CaseOutcome]) -> bool:
    print(f"\n{'=' * 70}")
    print("threshold eval harness — replay report")
    print(f"{'=' * 70}\n")
    for outcome in outcomes:
        status = "PASS" if outcome.passed else "FAIL"
        print(f"[{status}] #{outcome.case_id:2d}  {outcome.name}")
        for failure in outcome.failures:
            print(f"        - {failure}")
    passed_count = sum(1 for outcome in outcomes if outcome.passed)
    total = len(outcomes)
    print(f"\n{passed_count}/{total} cases passed\n")
    return passed_count == total


async def main() -> int:
    db_config = load_db_config()
    pool = await asyncpg.create_pool(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
        min_size=1,
        max_size=4,
    )
    clinic_configs = load_clinic_configs(CLINICS_DIR)
    cases = _load_cases()

    outcomes: list[CaseOutcome] = []
    try:
        for case in cases:
            outcome = await _run_case(pool, case, clinic_configs)
            outcomes.append(outcome)
    finally:
        await pool.close()

    all_passed = _print_report(outcomes)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
