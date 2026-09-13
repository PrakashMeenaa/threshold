import os
import uuid
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio


@dataclass
class DbConfig:
    host: str
    port: int
    database: str
    password: str


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


@pytest_asyncio.fixture
async def api_conn() -> asyncpg.Connection:
    db_config = load_db_config()
    conn = await asyncpg.connect(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
    )
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def clinic_ids(api_conn: asyncpg.Connection) -> dict[str, uuid.UUID]:
    sunrise_id: uuid.UUID = await api_conn.fetchval(
        "SELECT resolve_clinic_by_phone($1)", "sunrise-main"
    )
    city_dental_id: uuid.UUID = await api_conn.fetchval(
        "SELECT resolve_clinic_by_phone($1)", "city-dental-main"
    )
    assert sunrise_id is not None
    assert city_dental_id is not None
    return {"sunrise": sunrise_id, "city_dental": city_dental_id}


@pytest.mark.asyncio
async def test_missing_clinic_context_returns_zero_rows(
    api_conn: asyncpg.Connection,
) -> None:
    rows = await api_conn.fetch("SELECT id FROM doctors")
    assert rows == []


@pytest.mark.asyncio
async def test_scoped_session_only_sees_own_clinic(
    api_conn: asyncpg.Connection, clinic_ids: dict[str, uuid.UUID]
) -> None:
    async with api_conn.transaction():
        await api_conn.execute(
            "SELECT set_config('app.current_clinic_id', $1, true)",
            str(clinic_ids["sunrise"]),
        )
        doctor_rows = await api_conn.fetch("SELECT clinic_id FROM doctors")
        assert len(doctor_rows) == 2
        assert all(row["clinic_id"] == clinic_ids["sunrise"] for row in doctor_rows)

        clinic_rows = await api_conn.fetch("SELECT id FROM clinics")
        assert len(clinic_rows) == 1
        assert clinic_rows[0]["id"] == clinic_ids["sunrise"]


@pytest.mark.asyncio
async def test_cross_tenant_insert_is_rejected(
    api_conn: asyncpg.Connection, clinic_ids: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(asyncpg.PostgresError) as exc_info:
        async with api_conn.transaction():
            await api_conn.execute(
                "SELECT set_config('app.current_clinic_id', $1, true)",
                str(clinic_ids["sunrise"]),
            )
            await api_conn.execute(
                "INSERT INTO departments (clinic_id, name) VALUES ($1, $2)",
                clinic_ids["city_dental"],
                "Cross Tenant Attempt",
            )
    assert exc_info.value.sqlstate == "42501"


@pytest.mark.asyncio
async def test_audit_tables_are_immutable(api_conn: asyncpg.Connection) -> None:
    fake_id = uuid.uuid4()

    with pytest.raises(asyncpg.PostgresError) as update_exc_info:
        await api_conn.execute(
            "UPDATE consents SET granted = false WHERE id = $1", fake_id
        )
    assert update_exc_info.value.sqlstate == "42501"

    with pytest.raises(asyncpg.PostgresError) as delete_exc_info:
        await api_conn.execute("DELETE FROM consents WHERE id = $1", fake_id)
    assert delete_exc_info.value.sqlstate == "42501"
