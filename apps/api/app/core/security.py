"""Password hashing, JWT access tokens, opaque refresh tokens.

Passwords use scrypt from the standard library (memory-hard, OWASP-accepted).
The stored format carries its own parameters, so the cost can be raised later
and Argon2id can be introduced without breaking old hashes.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt

_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 128 * 1024 * 1024
MIN_PASSWORD_LEN = 10


class TokenError(Exception):
    """Access token is missing, malformed, expired or tampered with."""


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def check_password_policy(password: str) -> list:
    problems = []
    if len(password) < MIN_PASSWORD_LEN:
        problems.append(f"password must be at least {MIN_PASSWORD_LEN} characters")
    if password.isdigit():
        problems.append("password must not be only digits")
    if len(password) > 256:
        problems.append("password is too long")
    return problems


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(_unb64(dk)), maxmem=_MAXMEM)
        return hmac.compare_digest(calc, _unb64(dk))
    except (ValueError, TypeError):
        return False


def make_access_token(user_id: str, roles: list, secret: str, ttl: int = 900) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "roles": sorted(roles), "iat": now, "exp": now + ttl, "typ": "access"}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    try:
        data = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if data.get("typ") != "access":
        raise TokenError("wrong token type")
    return data


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Only this hash is stored; the token itself is never persisted."""
    return hashlib.sha256(token.encode()).hexdigest()
