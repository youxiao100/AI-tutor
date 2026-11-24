"""Session login for the workshop. Credentials come from the environment only."""

import hmac
import os
import secrets
import threading
import time
from typing import Dict, Optional

from fastapi import HTTPException, Request


COOKIE_NAME = "tutor_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 30
MAX_UPLOAD_BYTES = 1_500_000

_lock = threading.Lock()
_sessions: Dict[str, dict] = {}
_hits: Dict[str, list] = {}
_llm_lock = threading.Lock()


def admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"


def _admin_password() -> str:
    return os.environ.get("WORKSHOP_PASSWORD", "")


def login_configured() -> bool:
    return bool(_admin_password())


def credentials_match(username: str, password: str) -> bool:
    expected_user = admin_username()
    expected_password = _admin_password()
    if not expected_password:
        return False
    user_ok = hmac.compare_digest(username.encode("utf-8"), expected_user.encode("utf-8"))
    pass_ok = hmac.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8"))
    return user_ok and pass_ok


def _prune_locked(now: float) -> None:
    expired = [sid for sid, sess in _sessions.items() if now - sess["created_at"] > SESSION_TTL_SECONDS]
    for sid in expired:
        _sessions.pop(sid, None)
    if len(_sessions) <= MAX_SESSIONS:
        return
    oldest = sorted(_sessions.items(), key=lambda item: item[1]["created_at"])
    for sid, _sess in oldest[: len(_sessions) - MAX_SESSIONS]:
        _sessions.pop(sid, None)


def create_session(state: dict) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune_locked(now)
        _sessions[token] = {
            "created_at": now,
            "state": state,
        }
    return token


def drop_session(token: Optional[str]) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def session_state(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="请先登录")
    now = time.time()
    with _lock:
        sess = _sessions.get(token)
        if not sess or now - sess["created_at"] > SESSION_TTL_SECONDS:
            _sessions.pop(token, None)
            raise HTTPException(status_code=401, detail="请先登录")
        return sess["state"]


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def hit_limit(key: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    with _lock:
        recent = [stamp for stamp in _hits.get(key, []) if now - stamp < window_seconds]
        if len(recent) >= limit:
            raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
        recent.append(now)
        _hits[key] = recent


def acquire_llm_slot() -> None:
    if not _llm_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="已有一次出题正在进行，请稍后再试")


def release_llm_slot() -> None:
    _llm_lock.release()


def assert_same_origin(request: Request) -> None:
    # SameSite=Lax already withholds the session cookie on cross-site POST.
    # Reject browser requests that explicitly say they came from another site.
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="跨站请求已拒绝")
