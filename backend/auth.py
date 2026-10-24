"""Cookie sessions with HMAC-signed tokens. Defaults to Operator when authentication is not required."""
import base64
import hashlib
import hmac
import json
import time

from fastapi import Request

from config import SECRET_KEY, USERS

COOKIE = "idesk_session"
TTL = 8 * 3600
DEFAULT_USER = "Operator"


def _sign(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def check_login(user: str, password: str) -> bool:
    expected = USERS.get(user)
    return expected is not None and hmac.compare_digest(expected.encode(), password.encode())


def make_token(user: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(dict(u=user, exp=int(time.time()) + TTL)).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_token(token: str | None) -> str | None:
    try:
        payload, sig = (token or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data["u"] if data["exp"] > time.time() else None
    except Exception:  # noqa: BLE001
        return None


def current_user(request: Request = None) -> str:
    if request:
        user = read_token(request.cookies.get(COOKIE))
        if user:
            return user
    return DEFAULT_USER
