"""Gateway authentication helpers.

Supports two modes:

1. Single-token mode (default, backward-compatible):
   Set PLATFORM_API_TOKEN to a single token string.
   All requests with that token are accepted; key_id is "default".

2. Multi-key mode:
   Set ALLOWED_API_KEYS to a JSON array of objects:
     [{"token": "...", "key_id": "...", "name": "..."}]
   Each key has a stable key_id logged in OTEL spans and Prometheus labels.
   PLATFORM_API_TOKEN is still accepted as a fallback entry with key_id "default".

Admin operations always use a single ADMIN_API_TOKEN string.
"""
import json
import os
from dataclasses import dataclass

from fastapi import HTTPException, Request

_PLATFORM_API_TOKEN = os.getenv("PLATFORM_API_TOKEN", "agenthub-client-token")
_ALLOWED_API_KEYS_RAW = os.getenv("ALLOWED_API_KEYS", "")


@dataclass(frozen=True)
class ApiKey:
    token: str
    key_id: str
    name: str


def _build_key_store() -> dict[str, ApiKey]:
    store: dict[str, ApiKey] = {}

    # Always include the single-token fallback entry.
    default = ApiKey(token=_PLATFORM_API_TOKEN, key_id="default", name="Default Client")
    store[default.token] = default

    if _ALLOWED_API_KEYS_RAW:
        try:
            entries = json.loads(_ALLOWED_API_KEYS_RAW)
            for entry in entries:
                key = ApiKey(
                    token=entry["token"],
                    key_id=entry["key_id"],
                    name=entry.get("name", entry["key_id"]),
                )
                store[key.token] = key
        except (json.JSONDecodeError, KeyError):
            pass  # Malformed env; fall back to default only.

    return store


_KEY_STORE: dict[str, ApiKey] = _build_key_store()


def extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip()


def require_client_key(request: Request) -> ApiKey:
    """Validate the client bearer token and return the matched ApiKey.

    Raises HTTP 401 if the token is missing or unknown.
    The returned ApiKey carries key_id and name for tracing.
    """
    token = extract_bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="Missing bearer token for gateway")
    key = _KEY_STORE.get(token)
    if key is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token for gateway")
    return key


def require_bearer_token(request: Request, expected_token: str, realm: str) -> None:
    """Validate a single expected token. Used for admin operations."""
    token = extract_bearer_token(request)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail=f"Missing bearer token for {realm}",
        )
    if token != expected_token:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid bearer token for {realm}",
        )
