import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import yaml
from pydantic import BaseModel, ConfigDict

CLINICS_DIR = Path(__file__).resolve().parent.parent / "clinics"
SLOT_COUNT = 3
SLOT_DURATION_MINUTES = 30
SLOT_HOUR_UTC = 10


class DoctorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class DepartmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    doctors: list[DoctorConfig]


class ClinicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    slug: str
    phone_number_id: str
    languages: list[str]
    greeting: str
    escalation_rule: str
    departments: list[DepartmentConfig]


@dataclass
class DbConfig:
    host: str
    port: int
    database: str
    password: str


def load_clinic_config(path: Path) -> ClinicConfig:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return ClinicConfig.model_validate(raw)


def load_db_config() -> DbConfig:
    password = os.environ.get("THRESHOLD_API_PASSWORD")
    if not password:
        raise RuntimeError("THRESHOLD_API_PASSWORD must be set")
    return DbConfig(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5433")),
        database=os.environ.get("PGDATABASE", "threshold"),
        password=password,
    )


def future_slot_bounds(index: int) -> tuple[datetime, datetime]:
    start = datetime.now(timezone.utc).replace(
        hour=SLOT_HOUR_UTC, minute=0, second=0, microsecond=0
    ) + timedelta(days=index + 1)
    end = start + timedelta(minutes=SLOT_DURATION_MINUTES)
    return start, end


async def upsert_clinic(conn: asyncpg.Connection, clinic: ClinicConfig) -> uuid.UUID:
    existing_id: uuid.UUID | None = await conn.fetchval(
        "SELECT resolve_clinic_by_phone($1)", clinic.phone_number_id
    )
    clinic_id = existing_id if existing_id is not None else uuid.uuid4()
    await conn.execute(
        "SELECT set_config('app.current_clinic_id', $1, true)", str(clinic_id)
    )
    await conn.execute(
        """
        INSERT INTO clinics (id, name, slug, whatsapp_phone_number_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            slug = EXCLUDED.slug,
            whatsapp_phone_number_id = EXCLUDED.whatsapp_phone_number_id
        """,
        clinic_id,
        clinic.name,
        clinic.slug,
        clinic.phone_number_id,
    )
    return clinic_id


async def upsert_department(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, department: DepartmentConfig
) -> uuid.UUID:
    department_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO departments (clinic_id, name)
        VALUES ($1, $2)
        ON CONFLICT (clinic_id, name) DO UPDATE SET name = EXCLUDED.name
        RETURNING id
        """,
        clinic_id,
        department.name,
    )
    return department_id


async def upsert_doctor(
    conn: asyncpg.Connection,
    clinic_id: uuid.UUID,
    department_id: uuid.UUID,
    doctor: DoctorConfig,
) -> uuid.UUID:
    existing_id: uuid.UUID | None = await conn.fetchval(
        """
        SELECT id FROM doctors
        WHERE clinic_id = $1 AND department_id = $2 AND name = $3
        """,
        clinic_id,
        department_id,
        doctor.name,
    )
    if existing_id is not None:
        return existing_id
    inserted_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO doctors (clinic_id, department_id, name)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        clinic_id,
        department_id,
        doctor.name,
    )
    return inserted_id


async def seed_availability_slots(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, doctor_id: uuid.UUID
) -> None:
    for index in range(SLOT_COUNT):
        starts_at, ends_at = future_slot_bounds(index)
        await conn.execute(
            """
            INSERT INTO availability_slots (clinic_id, doctor_id, starts_at, ends_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (doctor_id, starts_at) DO NOTHING
            """,
            clinic_id,
            doctor_id,
            starts_at,
            ends_at,
        )


async def seed_clinic(conn: asyncpg.Connection, clinic: ClinicConfig) -> None:
    async with conn.transaction():
        clinic_id = await upsert_clinic(conn, clinic)
        for department in clinic.departments:
            department_id = await upsert_department(conn, clinic_id, department)
            for doctor in department.doctors:
                doctor_id = await upsert_doctor(conn, clinic_id, department_id, doctor)
                await seed_availability_slots(conn, clinic_id, doctor_id)


async def main() -> None:
    db_config = load_db_config()
    conn = await asyncpg.connect(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
    )
    try:
        for path in sorted(CLINICS_DIR.glob("*.yaml")):
            clinic = load_clinic_config(path)
            await seed_clinic(conn, clinic)
            print(f"Seeded {clinic.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
