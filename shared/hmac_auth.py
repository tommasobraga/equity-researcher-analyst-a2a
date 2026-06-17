"""HMAC-SHA256 authentication for A2A inter-agent calls.

Ogni chiamata POST /tasks deve includere:
  X-A2A-Timestamp: Unix timestamp UTC (secondi, stringa)
  X-A2A-Signature: HMAC-SHA256(secret, "{timestamp}.{body_bytes}")

Finestra anti-replay: ±30 secondi. Richieste fuori finestra → HTTP 401.

Il secret condiviso è letto da shared/secrets.py con chiave A2A_SHARED_SECRET.
In locale viene letto da .env; in produzione da Azure Key Vault o AWS Secrets Manager.
"""
import hashlib
import hmac
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
    """FastAPI middleware — verifica firma HMAC su ogni POST /tasks."""

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST" and request.url.path == "/tasks":
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
            try:
                secret = get_secret("A2A_SHARED_SECRET")
            except (KeyError, EnvironmentError):
                # Se il secret non è configurato (es. dev senza HMAC), passa attraverso
                return await call_next(request)

            expected = _compute_signature(secret, timestamp, body)
            if not hmac.compare_digest(expected, signature):
                return JSONResponse({"error": "Invalid signature"}, status_code=401)

        return await call_next(request)
