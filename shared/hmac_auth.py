"""HMAC-SHA256 authentication for A2A inter-agent calls.

Every POST /tasks call must include:
  X-A2A-Timestamp: Unix UTC timestamp (seconds, string)
  X-A2A-Signature: HMAC-SHA256(secret, "{timestamp}.{body_bytes}")

Anti-replay window: ±30 seconds. Requests outside the window → HTTP 401.

The shared secret is read from shared/secrets.py under key A2A_SHARED_SECRET.
Locally read from environment variables; in production from Azure Key Vault or AWS Secrets Manager.
"""
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from shared.secrets import get_secret

_REPLAY_WINDOW_SECONDS = 30


def _compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_request(body: bytes) -> dict[str, str]:
    """Return headers dict with X-A2A-Timestamp and X-A2A-Signature."""
    secret = get_secret("A2A_SHARED_SECRET")
    timestamp = str(int(time.time()))
    signature = _compute_signature(secret, timestamp, body)
    return {"X-A2A-Timestamp": timestamp, "X-A2A-Signature": signature}


class HMACMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware — verifies HMAC signature on every POST /tasks."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/tasks":
            # If A2A_SHARED_SECRET is not set, HMAC is disabled
            if not os.getenv("A2A_SHARED_SECRET"):
                return await call_next(request)
            secret = get_secret("A2A_SHARED_SECRET")

            timestamp = request.headers.get("X-A2A-Timestamp")
            signature = request.headers.get("X-A2A-Signature")

            if not timestamp or not signature:
                return JSONResponse(
                    {"error": "Missing X-A2A-Timestamp or X-A2A-Signature header"},
                    status_code=401,
                )

            try:
                ts = int(timestamp)
            except ValueError:
                return JSONResponse({"error": "Invalid X-A2A-Timestamp"}, status_code=401)

            if abs(time.time() - ts) > _REPLAY_WINDOW_SECONDS:
                return JSONResponse({"error": "Request timestamp outside replay window"}, status_code=401)

            body = await request.body()
            expected = _compute_signature(secret, timestamp, body)
            if not hmac.compare_digest(expected, signature):
                return JSONResponse({"error": "Invalid signature"}, status_code=401)

        return await call_next(request)
