import hashlib
import hmac
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import app

WEBHOOK_SECRET = "test-webhook-secret"
PAYLOAD_BODY = b'{"phone_number_id":"sunrise-main","from":"+10000000000","text":"hello","timestamp":1739000000}'


def signature_header(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_valid_signature_returns_ok(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header(WEBHOOK_SECRET, PAYLOAD_BODY),
    }
    response = await client.post("/webhook", content=PAYLOAD_BODY, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": signature_header("wrong-secret", PAYLOAD_BODY),
    }
    response = await client.post("/webhook", content=PAYLOAD_BODY, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    headers = {"Content-Type": "application/json"}
    response = await client.post("/webhook", content=PAYLOAD_BODY, headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_secret_returns_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    headers = {"Content-Type": "application/json"}
    response = await client.post("/webhook", content=PAYLOAD_BODY, headers=headers)
    assert response.status_code == 500
