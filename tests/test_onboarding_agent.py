import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio
from main import load_db_config
from onboarding_agent import (
    ClinicRuntimeConfig,
    ConsentExtraction,
    DepartmentExtraction,
    ExtractionFailure,
    LanguageExtraction,
    NameExtraction,
    OnboardingStep,
    advance_onboarding,
    load_onboarding_state,
)

SUNRISE_CONFIG = ClinicRuntimeConfig(
    greeting="Welcome to Sunrise!",
    consent_text="Do you consent? Reply YES or NO.",
    languages=["en", "hi"],
)


@dataclass
class FakeSlotExtractor:
    name_result: NameExtraction | None = None
    language_result: LanguageExtraction | None = None
    consent_result: ConsentExtraction | None = None
    department_result: DepartmentExtraction | None = None
    fail: bool = False

    async def extract_name(self, text: str) -> NameExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.name_result is not None
        return self.name_result

    async def extract_language(
        self, text: str, available_languages: list[str]
    ) -> LanguageExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.language_result is not None
        return self.language_result

    async def extract_consent(self, text: str) -> ConsentExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.consent_result is not None
        return self.consent_result

    async def extract_department(
        self, text: str, available_departments: list[str]
    ) -> DepartmentExtraction:
        if self.fail:
            raise ExtractionFailure
        assert self.department_result is not None
        return self.department_result


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
    patient_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO patients (clinic_id, whatsapp_id) VALUES ($1, $2) RETURNING id",
        clinic_id,
        f"+1{uuid.uuid4().int % 10**10}",
    )
    return patient_id


@pytest.mark.asyncio
async def test_full_happy_path_completes_onboarding(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)

    loaded = await load_onboarding_state(conn, clinic_id, patient_id)
    assert loaded.is_new is True
    assert loaded.state.step == OnboardingStep.AWAITING_NAME

    extractor = FakeSlotExtractor(name_result=NameExtraction(name="Asha Patel"))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, loaded.state, "hi, I'm Asha Patel", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_LANGUAGE
    assert result.persisted.patient_name == "Asha Patel"
    full_name = await conn.fetchval("SELECT full_name FROM patients WHERE id = $1", patient_id)
    assert full_name == "Asha Patel"

    state = (await load_onboarding_state(conn, clinic_id, patient_id)).state
    extractor = FakeSlotExtractor(language_result=LanguageExtraction(language="hi"))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, state, "hindi please", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_CONSENT
    assert result.reply_text == SUNRISE_CONFIG.consent_text
    assert result.persisted.patient_language == "hi"

    state = (await load_onboarding_state(conn, clinic_id, patient_id)).state
    extractor = FakeSlotExtractor(consent_result=ConsentExtraction(decision="affirmative"))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, state, "yes", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_DEPARTMENT
    assert result.persisted.consent_granted is True
    consent_row = await conn.fetchrow(
        "SELECT granted, presented_text FROM consents WHERE patient_id = $1", patient_id
    )
    assert consent_row is not None
    assert consent_row["granted"] is True
    assert consent_row["presented_text"] == SUNRISE_CONFIG.consent_text

    state = (await load_onboarding_state(conn, clinic_id, patient_id)).state
    extractor = FakeSlotExtractor(department_result=DepartmentExtraction(department="Cardiology"))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, state, "cardiology", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.COMPLETE
    assert result.persisted.department == "Cardiology"
    assert "Asha Patel" in result.reply_text


@pytest.mark.asyncio
async def test_consent_decline_records_and_reprompts(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    loaded = await load_onboarding_state(conn, clinic_id, patient_id)

    from onboarding_agent import OnboardingState

    state = OnboardingState(
        step=OnboardingStep.AWAITING_CONSENT,
        name="Ravi",
        language="en",
        department=None,
        unclear_count=0,
    )
    extractor = FakeSlotExtractor(consent_result=ConsentExtraction(decision="decline"))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, state, "no thanks", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_CONSENT
    assert result.persisted.consent_granted is False
    consent_row = await conn.fetchrow(
        "SELECT granted FROM consents WHERE patient_id = $1", patient_id
    )
    assert consent_row is not None
    assert consent_row["granted"] is False
    assert loaded.is_new is True


@pytest.mark.asyncio
async def test_unclear_department_bounded_reprompt(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    await load_onboarding_state(conn, clinic_id, patient_id)

    from onboarding_agent import MAX_UNCLEAR_ATTEMPTS, OnboardingState, save_onboarding_state

    state = OnboardingState(
        step=OnboardingStep.AWAITING_DEPARTMENT,
        name="Ravi",
        language="en",
        department=None,
        unclear_count=0,
    )
    await save_onboarding_state(conn, clinic_id, patient_id, state)
    extractor = FakeSlotExtractor(department_result=DepartmentExtraction(department=None))
    for _ in range(MAX_UNCLEAR_ATTEMPTS + 2):
        result = await advance_onboarding(
            conn, clinic_id, patient_id, state, "umm not sure", SUNRISE_CONFIG, extractor
        )
        assert result.next_step == OnboardingStep.AWAITING_DEPARTMENT
        state = (await load_onboarding_state(conn, clinic_id, patient_id)).state

    assert state.unclear_count == MAX_UNCLEAR_ATTEMPTS
    assert "exact department name" in result.reply_text


@pytest.mark.asyncio
async def test_repeated_decline_is_bounded_and_deduplicated(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    await load_onboarding_state(conn, clinic_id, patient_id)

    from onboarding_agent import MAX_UNCLEAR_ATTEMPTS, OnboardingState, save_onboarding_state

    state = OnboardingState(
        step=OnboardingStep.AWAITING_CONSENT,
        name="Ravi",
        language="en",
        department=None,
        unclear_count=0,
    )
    await save_onboarding_state(conn, clinic_id, patient_id, state)

    extractor = FakeSlotExtractor(consent_result=ConsentExtraction(decision="decline"))
    for _ in range(MAX_UNCLEAR_ATTEMPTS + 2):
        result = await advance_onboarding(
            conn, clinic_id, patient_id, state, "no", SUNRISE_CONFIG, extractor
        )
        assert result.next_step == OnboardingStep.AWAITING_CONSENT
        state = (await load_onboarding_state(conn, clinic_id, patient_id)).state

    consent_count = await conn.fetchval(
        "SELECT count(*) FROM consents WHERE patient_id = $1", patient_id
    )
    assert consent_count == 1
    assert state.unclear_count == MAX_UNCLEAR_ATTEMPTS
    assert "staff member" in result.reply_text


@pytest.mark.asyncio
async def test_language_matching_is_case_and_whitespace_insensitive(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    await load_onboarding_state(conn, clinic_id, patient_id)

    from onboarding_agent import OnboardingState, save_onboarding_state

    state = OnboardingState(
        step=OnboardingStep.AWAITING_LANGUAGE,
        name="Ravi",
        language=None,
        department=None,
        unclear_count=0,
    )
    await save_onboarding_state(conn, clinic_id, patient_id, state)

    extractor = FakeSlotExtractor(language_result=LanguageExtraction(language="  EN "))
    result = await advance_onboarding(
        conn, clinic_id, patient_id, state, "English please", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_CONSENT
    assert result.persisted.patient_language == "en"
    stored = await conn.fetchval(
        "SELECT preferred_language FROM patients WHERE id = $1", patient_id
    )
    assert stored == "en"


@pytest.mark.asyncio
async def test_extraction_failure_does_not_advance(
    scoped_conn: tuple[asyncpg.Connection, uuid.UUID],
) -> None:
    conn, clinic_id = scoped_conn
    patient_id = await create_patient(conn, clinic_id)
    loaded = await load_onboarding_state(conn, clinic_id, patient_id)

    extractor = FakeSlotExtractor(fail=True)
    result = await advance_onboarding(
        conn, clinic_id, patient_id, loaded.state, "whatever", SUNRISE_CONFIG, extractor
    )
    assert result.next_step == OnboardingStep.AWAITING_NAME
    assert result.persisted == result.persisted.__class__()
    full_name = await conn.fetchval("SELECT full_name FROM patients WHERE id = $1", patient_id)
    assert full_name is None
