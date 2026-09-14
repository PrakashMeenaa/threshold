import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import MAX_BODY_BYTES, app, load_db_config
from safety_gate import EMERGENCY_REPLY, MEDICAL_ADVICE_REPLY

WEBHOOK_SECRET = "test-webhook-secret-" + "x" * 20


def build_payload_body(
    phone_number_id: str, from_number: str, text: str, timestamp: int
) -> bytes:
    return json.dumps(
        {
            "phone_number_id": phone_number_id,
            "from": from_number,
            "text": text,
            "timestamp": timestamp,
        }
    ).encode("utf-8")


def signature_header(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as async_client:
            yield async_client


async def open_scoped_connection(phone_number_id: str) -> asyncpg.Connection:
    db_config = load_db_config()
    conn = await asyncpg.connect(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
    )
    clinic_id = await conn.fetchval("SELECT resolve_clinic_by_phone($1)", phone_number_id)
    await conn.execute("SELECT set_config('app.current_clinic_id', $1, false)", str(clinic_id))
    return conn


async def count_gate_log_rows(conn: asyncpg.Connection, category: str) -> int:
    count: int = await conn.fetchval(
        "SELECT count(*) FROM gate_log WHERE category = $1", category
    )
    return count


@pytest.mark.asyncio
async def test_valid_signature_returns_ok(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main", "+10000000000", f"hello-{uuid.uuid4()}", int(time.time())
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main", "+10000000000", f"hello-{uuid.uuid4()}", int(time.time())
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header("wrong-secret", body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main", "+10000000000", f"hello-{uuid.uuid4()}", int(time.time())
    )
    headers = {"Content-Type": "application/json"}
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_secret_returns_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    body = build_payload_body(
        "sunrise-main", "+10000000000", f"hello-{uuid.uuid4()}", int(time.time())
    )
    headers = {"Content-Type": "application/json"}
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 500


@pytest.mark.asyncio
async def test_duplicate_payload_is_blocked(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main",
        "+10000000001",
        f"duplicate-check-{uuid.uuid4()}",
        int(time.time()),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }

    first_response = await client.post("/webhook", content=body, headers=headers)
    assert first_response.status_code == 200
    assert first_response.json() == {"status": "ok"}

    second_response = await client.post("/webhook", content=body, headers=headers)
    assert second_response.status_code == 200
    assert second_response.json() == {"status": "ok", "detail": "already_processed"}


@pytest.mark.asyncio
async def test_oversized_payload_returns_413(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = b"x" * (MAX_BODY_BYTES + 1)
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_non_string_phone_number_id_returns_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = json.dumps(
        {
            "phone_number_id": 12345,
            "from": "+10000000000",
            "text": f"hello-{uuid.uuid4()}",
            "timestamp": int(time.time()),
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_clinic_returns_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "unknown-clinic-phone-id",
        "+10000000000",
        f"hello-{uuid.uuid4()}",
        int(time.time()),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_empty_text_returns_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body("sunrise-main", "+10000000000", "", int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }
    response = await client.post("/webhook", content=body, headers=headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_emergency_message_returns_reply_and_logs_gate_event(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main",
        "+10000000002",
        f"I have crushing chest pain right now {uuid.uuid4()}",
        int(time.time()),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }

    conn = await open_scoped_connection("sunrise-main")
    try:
        before_count = await count_gate_log_rows(conn, "chest_pain_cardiac")

        response = await client.post("/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"reply": EMERGENCY_REPLY}

        after_count = await count_gate_log_rows(conn, "chest_pain_cardiac")
        assert after_count == before_count + 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_medical_advice_message_returns_reply_and_logs_gate_event(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "city-dental-main",
        "+10000000003",
        f"what medicine should I take for a toothache {uuid.uuid4()}",
        int(time.time()),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }

    conn = await open_scoped_connection("city-dental-main")
    try:
        before_count = await count_gate_log_rows(conn, "medical_advice_request")

        response = await client.post("/webhook", content=body, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"reply": MEDICAL_ADVICE_REPLY}

        after_count = await count_gate_log_rows(conn, "medical_advice_request")
        assert after_count == before_count + 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_duplicate_emergency_message_does_not_double_log(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    body = build_payload_body(
        "sunrise-main",
        "+10000000004",
        f"I have crushing chest pain and can't breathe {uuid.uuid4()}",
        int(time.time()),
    )
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, body),
    }

    conn = await open_scoped_connection("sunrise-main")
    try:
        before_count = await count_gate_log_rows(conn, "chest_pain_cardiac")

        first_response = await client.post("/webhook", content=body, headers=headers)
        assert first_response.status_code == 200
        assert first_response.json() == {"reply": EMERGENCY_REPLY}

        after_first_count = await count_gate_log_rows(conn, "chest_pain_cardiac")
        assert after_first_count == before_count + 1

        second_response = await client.post("/webhook", content=body, headers=headers)
        assert second_response.status_code == 200
        assert second_response.json() == {"status": "ok", "detail": "already_processed"}

        after_second_count = await count_gate_log_rows(conn, "chest_pain_cardiac")
        assert after_second_count == after_first_count
    finally:
        await conn.close()
