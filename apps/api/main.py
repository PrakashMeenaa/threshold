import hashlib
import hmac
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

app = FastAPI()


class WebhookPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    phone_number_id: str
    from_: str = Field(alias="from")
    text: str
    timestamp: int


async def verify_signature(request: Request) -> None:
    secret = os.environ.get("WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="Internal server error")

    signature_header = request.headers.get("X-Hub-Signature-256")
    if signature_header is None or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")

    provided_digest = signature_header.removeprefix("sha256=")

    body = await request.body()
    expected_digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(provided_digest, expected_digest):
        raise HTTPException(status_code=401, detail="Invalid signature")


@app.post("/webhook", dependencies=[Depends(verify_signature)])
async def receive_webhook(payload: WebhookPayload) -> dict[str, str]:
    return {"status": "ok"}
