import hashlib
import hmac
import json
import re

from fastapi import HTTPException, Security, Request
from fastapi.security import APIKeyHeader
from .config import settings


USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_user_id(user_id: str) -> str:
    if not USER_ID_RE.fullmatch(user_id):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "INVALID_USER_ID",
                "message": "user_id must be 1-64 characters using letters, numbers, dot, underscore or hyphen.",
                "details": {},
            },
        )
    return user_id


def require_api_key(request: Request, x_api_key: str | None = Security(api_key_header)):
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": "INVALID_API_KEY",
                "message": "Missing API key.",
                "details": {},
            },
        )

    is_admin = hmac.compare_digest(x_api_key, settings.api_key)
    is_read_only = False
    if settings.read_only_api_key:
        is_read_only = hmac.compare_digest(x_api_key, settings.read_only_api_key)

    if not is_admin and not is_read_only:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": "INVALID_API_KEY",
                "message": "Invalid or missing API key.",
                "details": {},
            },
        )

    if "/actions/" in request.url.path:
        if not is_admin:
            raise HTTPException(
                status_code=403,
                detail={
                    "ok": False,
                    "error": "WRITE_ACCESS_DENIED",
                    "message": "This API key does not have write permissions.",
                    "details": {},
                },
            )
    return True


def require_admin_api_key(x_api_key: str | None = Security(api_key_header)):
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "ok": False,
                "error": "INVALID_API_KEY",
                "message": "Missing API key.",
                "details": {},
            },
        )

    if hmac.compare_digest(x_api_key, settings.api_key):
        return True

    if settings.read_only_api_key and hmac.compare_digest(x_api_key, settings.read_only_api_key):
        raise HTTPException(
            status_code=403,
            detail={
                "ok": False,
                "error": "WRITE_ACCESS_DENIED",
                "message": "This API key does not have access to private user workspaces.",
                "details": {},
            },
        )

    raise HTTPException(
        status_code=401,
        detail={
            "ok": False,
            "error": "INVALID_API_KEY",
            "message": "Invalid or missing API key.",
            "details": {},
        },
    )


def ensure_write_allowed():
    return None


def require_confirmation(expected: str, received: str | None):
    if settings.require_write_confirmation and received != expected:
        return {
            "ok": False,
            "error": "WRITE_CONFIRMATION_REQUIRED",
            "message": "This action requires explicit confirmation.",
            "details": {"expected": expected},
        }
    return None


def lineup_confirmation_token(user_id: str, payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(f"{user_id}:{canonical}".encode("utf-8")).hexdigest()[:16]
    return f"CONFIRM_LINEUP_{digest}"
