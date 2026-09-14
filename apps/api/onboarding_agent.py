import json
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol, TypeVar

import anthropic
import asyncpg
import yaml
from pydantic import BaseModel, ConfigDict

CONSENT_TYPE_ONBOARDING = "onboarding_data_processing"
MAX_UNCLEAR_ATTEMPTS = 3
EXTRACTION_FAILURE_REPLY = "Sorry, I had trouble understanding that — could you try again?"
CONSENT_ESCALATION_REPLY = (
    "I still need your consent to continue — reply YES if you'd like to proceed, "
    "or a staff member can follow up with you directly."
)
ONBOARDING_MODEL = "claude-sonnet-5"
EXTRACTION_TIMEOUT_SECONDS = 20.0


class OnboardingStep(str, Enum):
    AWAITING_NAME = "awaiting_name"
    AWAITING_LANGUAGE = "awaiting_language"
    AWAITING_CONSENT = "awaiting_consent"
    AWAITING_DEPARTMENT = "awaiting_department"
    COMPLETE = "complete"


class NameExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None


class LanguageExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str | None


class ConsentExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["affirmative", "decline", "unclear"]


class DepartmentExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department: str | None


class ExtractionFailure(Exception):
    pass


class SlotExtractor(Protocol):
    async def extract_name(self, text: str) -> NameExtraction: ...

    async def extract_language(
        self, text: str, available_languages: list[str]
    ) -> LanguageExtraction: ...

    async def extract_consent(self, text: str) -> ConsentExtraction: ...

    async def extract_department(
        self, text: str, available_departments: list[str]
    ) -> DepartmentExtraction: ...


@dataclass
class OnboardingState:
    step: OnboardingStep
    name: str | None
    language: str | None
    department: str | None
    unclear_count: int


@dataclass
class LoadedOnboardingState:
    state: OnboardingState
    is_new: bool


@dataclass
class ClinicRuntimeConfig:
    greeting: str
    consent_text: str
    languages: list[str]


@dataclass
class PersistedChanges:
    patient_name: str | None = None
    patient_language: str | None = None
    consent_granted: bool | None = None
    department: str | None = None


@dataclass
class TurnResult:
    next_step: OnboardingStep
    reply_text: str
    persisted: PersistedChanges


T = TypeVar("T", bound=BaseModel)


class AnthropicSlotExtractor:
    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client

    async def extract_name(self, text: str) -> NameExtraction:
        return await self._parse(
            NameExtraction,
            "Extract the patient's name from their WhatsApp message to a clinic. "
            "If no name is clearly stated, set name to null.",
            text,
        )

    async def extract_language(
        self, text: str, available_languages: list[str]
    ) -> LanguageExtraction:
        return await self._parse(
            LanguageExtraction,
            "Extract which of these exact language codes the patient wants: "
            f"{available_languages}. If their message does not clearly match one of "
            "these exact values, set language to null.",
            text,
        )

    async def extract_consent(self, text: str) -> ConsentExtraction:
        return await self._parse(
            ConsentExtraction,
            "Classify the patient's reply to a data-processing consent question as "
            '"affirmative", "decline", or "unclear".',
            text,
        )

    async def extract_department(
        self, text: str, available_departments: list[str]
    ) -> DepartmentExtraction:
        return await self._parse(
            DepartmentExtraction,
            "Extract which of these exact department names the patient wants: "
            f"{available_departments}. If their message does not clearly match one of "
            "these exact values, set department to null.",
            text,
        )

    async def _parse(self, output_format: type[T], instruction: str, text: str) -> T:
        try:
            response = await self._client.with_options(
                timeout=EXTRACTION_TIMEOUT_SECONDS
            ).messages.parse(
                model=ONBOARDING_MODEL,
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


def load_clinic_configs(clinics_dir: Path) -> dict[str, ClinicRuntimeConfig]:
    configs: dict[str, ClinicRuntimeConfig] = {}
    for path in sorted(clinics_dir.glob("*.yaml")):
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        configs[raw["phone_number_id"]] = ClinicRuntimeConfig(
            greeting=raw["greeting"],
            consent_text=raw["consent_text"],
            languages=raw["languages"],
        )
    return configs


def _match_option(value: str | None, options: list[str]) -> str | None:
    if value is None:
        return None
    normalized_options = {option.strip().casefold(): option for option in options}
    return normalized_options.get(value.strip().casefold())


def language_question(languages: list[str]) -> str:
    return f"Which language would you prefer: {', '.join(languages)}?"


def department_question(departments: list[str]) -> str:
    return f"Which department would you like to visit: {', '.join(departments)}?"


async def fetch_department_names(conn: asyncpg.Connection, clinic_id: uuid.UUID) -> list[str]:
    rows = await conn.fetch(
        "SELECT name FROM departments WHERE clinic_id = $1 ORDER BY name", clinic_id
    )
    return [row["name"] for row in rows]


def _state_to_json(state: OnboardingState) -> str:
    return json.dumps(
        {
            "step": state.step.value,
            "name": state.name,
            "language": state.language,
            "department": state.department,
            "unclear_count": state.unclear_count,
        }
    )


def _state_from_json(raw: str) -> OnboardingState:
    data = json.loads(raw)
    return OnboardingState(
        step=OnboardingStep(data["step"]),
        name=data["name"],
        language=data["language"],
        department=data["department"],
        unclear_count=data["unclear_count"],
    )


async def save_onboarding_state(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID, state: OnboardingState
) -> None:
    await conn.execute(
        """
        UPDATE conversation_state
        SET state = $1::jsonb, updated_at = now()
        WHERE clinic_id = $2 AND patient_id = $3 AND agent = 'onboarding'
        """,
        _state_to_json(state),
        clinic_id,
        patient_id,
    )


async def load_onboarding_state(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, patient_id: uuid.UUID
) -> LoadedOnboardingState:
    initial_state = OnboardingState(
        step=OnboardingStep.AWAITING_NAME, name=None, language=None, department=None, unclear_count=0
    )
    inserted_id: uuid.UUID | None = await conn.fetchval(
        """
        INSERT INTO conversation_state (clinic_id, patient_id, agent, state)
        VALUES ($1, $2, 'onboarding', $3::jsonb)
        ON CONFLICT (clinic_id, patient_id, agent) DO NOTHING
        RETURNING id
        """,
        clinic_id,
        patient_id,
        _state_to_json(initial_state),
    )
    is_new = inserted_id is not None

    row = await conn.fetchrow(
        """
        SELECT state FROM conversation_state
        WHERE clinic_id = $1 AND patient_id = $2 AND agent = 'onboarding'
        FOR UPDATE
        """,
        clinic_id,
        patient_id,
    )
    assert row is not None
    state = _state_from_json(row["state"])
    return LoadedOnboardingState(state=state, is_new=is_new)


async def _record_consent(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    presented_text: str,
    granted: bool,
) -> None:
    await conn.execute(
        """
        INSERT INTO consents (clinic_id, patient_id, consent_type, granted, presented_text)
        VALUES ($1, $2, $3, $4, $5)
        """,
        clinic_id,
        patient_id,
        CONSENT_TYPE_ONBOARDING,
        granted,
        presented_text,
    )


async def _handle_unclear(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: OnboardingState,
    normal_reply: str,
    bounded_reply: str,
) -> TurnResult:
    unclear_count = min(state.unclear_count + 1, MAX_UNCLEAR_ATTEMPTS)
    next_state = OnboardingState(
        step=state.step,
        name=state.name,
        language=state.language,
        department=state.department,
        unclear_count=unclear_count,
    )
    await save_onboarding_state(conn, clinic_id, patient_id, next_state)
    reply = bounded_reply if unclear_count >= MAX_UNCLEAR_ATTEMPTS else normal_reply
    return TurnResult(next_step=state.step, reply_text=reply, persisted=PersistedChanges())


async def _handle_decline(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: OnboardingState,
    clinic_config: ClinicRuntimeConfig,
) -> TurnResult:
    is_first_decline = state.unclear_count == 0
    if is_first_decline:
        await _record_consent(
            conn, clinic_id, patient_id, clinic_config.consent_text, granted=False
        )

    unclear_count = min(state.unclear_count + 1, MAX_UNCLEAR_ATTEMPTS)
    next_state = OnboardingState(
        step=state.step,
        name=state.name,
        language=state.language,
        department=state.department,
        unclear_count=unclear_count,
    )
    await save_onboarding_state(conn, clinic_id, patient_id, next_state)

    reply = (
        CONSENT_ESCALATION_REPLY
        if unclear_count >= MAX_UNCLEAR_ATTEMPTS
        else "No problem — reply anytime you'd like to continue."
    )
    persisted = PersistedChanges(consent_granted=False) if is_first_decline else PersistedChanges()
    return TurnResult(next_step=state.step, reply_text=reply, persisted=persisted)


async def advance_onboarding(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    patient_id: uuid.UUID,
    state: OnboardingState,
    message_text: str,
    clinic_config: ClinicRuntimeConfig,
    extractor: SlotExtractor,
) -> TurnResult:
    if state.step == OnboardingStep.AWAITING_NAME:
        try:
            extraction = await extractor.extract_name(message_text)
        except ExtractionFailure:
            return TurnResult(state.step, EXTRACTION_FAILURE_REPLY, PersistedChanges())

        name = extraction.name.strip() if extraction.name else None
        if not name:
            return TurnResult(
                state.step,
                "Sorry, I didn't catch a name there — could you tell me your name?",
                PersistedChanges(),
            )

        await conn.execute("UPDATE patients SET full_name = $1 WHERE id = $2", name, patient_id)
        next_state = OnboardingState(
            step=OnboardingStep.AWAITING_LANGUAGE,
            name=name,
            language=None,
            department=None,
            unclear_count=0,
        )
        await save_onboarding_state(conn, clinic_id, patient_id, next_state)
        return TurnResult(
            next_state.step,
            language_question(clinic_config.languages),
            PersistedChanges(patient_name=name),
        )

    if state.step == OnboardingStep.AWAITING_LANGUAGE:
        try:
            extraction = await extractor.extract_language(message_text, clinic_config.languages)
        except ExtractionFailure:
            return TurnResult(state.step, EXTRACTION_FAILURE_REPLY, PersistedChanges())

        matched_language = _match_option(extraction.language, clinic_config.languages)
        if matched_language is None:
            return TurnResult(
                state.step, language_question(clinic_config.languages), PersistedChanges()
            )

        await conn.execute(
            "UPDATE patients SET preferred_language = $1 WHERE id = $2",
            matched_language,
            patient_id,
        )
        next_state = OnboardingState(
            step=OnboardingStep.AWAITING_CONSENT,
            name=state.name,
            language=matched_language,
            department=None,
            unclear_count=0,
        )
        await save_onboarding_state(conn, clinic_id, patient_id, next_state)
        return TurnResult(
            next_state.step,
            clinic_config.consent_text,
            PersistedChanges(patient_language=matched_language),
        )

    if state.step == OnboardingStep.AWAITING_CONSENT:
        try:
            extraction = await extractor.extract_consent(message_text)
        except ExtractionFailure:
            return TurnResult(state.step, EXTRACTION_FAILURE_REPLY, PersistedChanges())

        if extraction.decision == "affirmative":
            await _record_consent(
                conn, clinic_id, patient_id, clinic_config.consent_text, granted=True
            )
            departments = await fetch_department_names(conn, clinic_id)
            next_state = OnboardingState(
                step=OnboardingStep.AWAITING_DEPARTMENT,
                name=state.name,
                language=state.language,
                department=None,
                unclear_count=0,
            )
            await save_onboarding_state(conn, clinic_id, patient_id, next_state)
            return TurnResult(
                next_state.step,
                department_question(departments),
                PersistedChanges(consent_granted=True),
            )

        if extraction.decision == "decline":
            return await _handle_decline(conn, clinic_id, patient_id, state, clinic_config)

        return await _handle_unclear(
            conn,
            clinic_id,
            patient_id,
            state,
            clinic_config.consent_text,
            "I still couldn't tell if that's a yes or no — please reply exactly YES or NO.",
        )

    if state.step == OnboardingStep.AWAITING_DEPARTMENT:
        departments = await fetch_department_names(conn, clinic_id)
        try:
            extraction = await extractor.extract_department(message_text, departments)
        except ExtractionFailure:
            return TurnResult(state.step, EXTRACTION_FAILURE_REPLY, PersistedChanges())

        matched_department = _match_option(extraction.department, departments)
        if matched_department is not None:
            await conn.execute(
                """
                UPDATE patients SET department_id = (
                    SELECT id FROM departments WHERE clinic_id = $1 AND name = $2
                )
                WHERE id = $3
                """,
                clinic_id,
                matched_department,
                patient_id,
            )
        if matched_department is None:
            return await _handle_unclear(
                conn,
                clinic_id,
                patient_id,
                state,
                department_question(departments),
                "I still couldn't match that to one of our departments "
                f"({', '.join(departments)}) — please reply with the exact department name.",
            )

        next_state = OnboardingState(
            step=OnboardingStep.COMPLETE,
            name=state.name,
            language=state.language,
            department=matched_department,
            unclear_count=0,
        )
        await save_onboarding_state(conn, clinic_id, patient_id, next_state)
        return TurnResult(
            next_state.step,
            f"Thanks, {state.name}! You're all set — we'll be in touch soon about "
            "booking your appointment.",
            PersistedChanges(department=matched_department),
        )

    return TurnResult(
        OnboardingStep.COMPLETE,
        "You're already registered — appointment booking is coming soon!",
        PersistedChanges(),
    )
