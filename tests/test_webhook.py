import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import MAX_BODY_BYTES, app

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
