import hashlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anthropic
import asyncpg
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from onboarding_agent import (
    AnthropicSlotExtractor,
    ClinicRuntimeConfig,
    SlotExtractor,
    advance_onboarding,
    load_clinic_configs,
    load_onboarding_state,
)
from safety_gate import action_for_category, evaluate

MAX_BODY_BYTES = 8192
MIN_SECRET_LENGTH = 32
CLINICS_DIR = Path(__file__).resolve().parent.parent.parent / "clinics"

db_pool: asyncpg.Pool | None = None
slot_extractor: SlotExtractor | None = None
clinic_configs: dict[str, ClinicRuntimeConfig] = {}


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        total_bytes = 0

        async def limited_receive() -> Message:
            nonlocal total_bytes
            message = await receive()
            if message["type"] == "http.request":
                total_bytes += len(message.get("body", b""))
                if total_bytes > self.max_bytes:
                    raise HTTPException(status_code=413, detail="Payload too large")
            return message

        await self.app(scope, limited_receive, send)


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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global db_pool, slot_extractor, clinic_configs
    db_config = load_db_config()
    db_pool = await asyncpg.create_pool(
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
        user="threshold_api_user",
        password=db_config.password,
        min_size=2,
        max_size=10,
        command_timeout=10,
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY must be set")
    slot_extractor = AnthropicSlotExtractor(anthropic.AsyncAnthropic())
    clinic_configs = load_clinic_configs(CLINICS_DIR)

    yield
    await db_pool.close()
    db_pool = None
    slot_extractor = None
    clinic_configs = {}


app = FastAPI(lifespan=lifespan)
app.add_middleware(MaxBodySizeMiddleware, max_bytes=MAX_BODY_BYTES)


class WebhookPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    phone_number_id: str = Field(min_length=1, max_length=128)
    from_: str = Field(alias="from", min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=4096)
    timestamp: int = Field(gt=0)


@dataclass
class WebhookContext:
    conn: asyncpg.Connection
    clinic_id: uuid.UUID


async def verify_signature(request: Request) -> None:
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret or len(secret) < MIN_SECRET_LENGTH:
        raise HTTPException(status_code=500, detail="Internal server error")

    signature_header = request.headers.get("X-Hub-Signature-256")
    if signature_header is None or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")

    provided_digest = signature_header.removeprefix("sha256=")

    body = await request.body()
    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided_digest, expected_digest):
        raise HTTPException(status_code=401, detail="Invalid signature")


async def get_webhook_context(request: Request) -> AsyncIterator[WebhookContext]:
    body = await request.body()
    try:
        parsed_body = json.loads(body)
        phone_number_id = parsed_body["phone_number_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise HTTPException(status_code=422, detail="Invalid payload")

    if not isinstance(phone_number_id, str):
        raise HTTPException(status_code=422, detail="Invalid payload")

    if db_pool is None:
        raise HTTPException(status_code=500, detail="Internal server error")

    async with db_pool.acquire() as conn:
        async with conn.transaction():
            clinic_id: uuid.UUID | None = await conn.fetchval(
                "SELECT resolve_clinic_by_phone($1)", phone_number_id
            )
            if clinic_id is None:
                raise HTTPException(status_code=404, detail="Unknown clinic")

            await conn.execute(
                "SELECT set_config('app.current_clinic_id', $1, true)",
                str(clinic_id),
            )

            yield WebhookContext(conn=conn, clinic_id=clinic_id)


def compute_payload_hash(payload: WebhookPayload) -> str:
    canonical = "\x1f".join(
        [payload.phone_number_id, payload.from_, payload.text, str(payload.timestamp)]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def record_if_new(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, payload_hash: str
) -> bool:
    inserted_id: uuid.UUID | None = await conn.fetchval(
        """
        INSERT INTO processed_webhooks (clinic_id, payload_hash)
        VALUES ($1, $2)
        ON CONFLICT (clinic_id, payload_hash) DO NOTHING
        RETURNING id
        """,
        clinic_id,
        payload_hash,
    )
    return inserted_id is None


async def log_gate_event(conn: asyncpg.Connection, clinic_id: uuid.UUID, category: str) -> None:
    await conn.execute(
        "INSERT INTO gate_log (clinic_id, category, action) VALUES ($1, $2, $3)",
        clinic_id,
        category,
        action_for_category(category),
    )


async def upsert_patient(
    conn: asyncpg.Connection, clinic_id: uuid.UUID, whatsapp_id: str
) -> uuid.UUID:
    patient_id: uuid.UUID = await conn.fetchval(
        """
        INSERT INTO patients (clinic_id, whatsapp_id)
        VALUES ($1, $2)
        ON CONFLICT (clinic_id, whatsapp_id) DO UPDATE SET whatsapp_id = EXCLUDED.whatsapp_id
        RETURNING id
        """,
        clinic_id,
        whatsapp_id,
    )
    return patient_id


@app.post("/webhook", dependencies=[Depends(verify_signature)])
async def receive_webhook(
    payload: WebhookPayload, ctx: WebhookContext = Depends(get_webhook_context)
) -> dict[str, str]:
    payload_hash = compute_payload_hash(payload)
    already_processed = await record_if_new(ctx.conn, ctx.clinic_id, payload_hash)
    if already_processed:
        return {"status": "ok", "detail": "already_processed"}

    gate_result = evaluate(payload.text)
    if gate_result.triggered:
        assert gate_result.category is not None
        assert gate_result.reply_text is not None
        await log_gate_event(ctx.conn, ctx.clinic_id, gate_result.category)
        return {"reply": gate_result.reply_text}

    if slot_extractor is None:
        raise HTTPException(status_code=500, detail="Internal server error")
    clinic_config = clinic_configs.get(payload.phone_number_id)
    if clinic_config is None:
        raise HTTPException(status_code=500, detail="Internal server error")

    patient_id = await upsert_patient(ctx.conn, ctx.clinic_id, payload.from_)
    loaded = await load_onboarding_state(ctx.conn, ctx.clinic_id, patient_id)
    turn_result = await advance_onboarding(
        ctx.conn,
        ctx.clinic_id,
        patient_id,
        loaded.state,
        payload.text,
        clinic_config,
        slot_extractor,
    )

    reply_text = turn_result.reply_text
    if loaded.is_new:
        reply_text = f"{clinic_config.greeting}\n\n{reply_text}"
    return {"reply": reply_text}
