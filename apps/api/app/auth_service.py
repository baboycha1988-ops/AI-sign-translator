"""Registration, login and refresh-token rotation with reuse detection."""
import re
import time
import uuid
from dataclasses import dataclass

from .core import security
from .repo import RefreshRecord

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthService:
    def __init__(self, repo, jwt_secret: str, *, access_ttl=900, refresh_ttl=30 * 86400,
                 dev_auto_verify=False, clock=time.time):
        if len(jwt_secret) < 32:
            raise ValueError("JWT secret must be at least 32 characters")
        self.repo, self.secret = repo, jwt_secret
        self.access_ttl, self.refresh_ttl = access_ttl, refresh_ttl
        self.dev_auto_verify, self.clock = dev_auto_verify, clock
        self._dummy = security.hash_password("dummy-password-for-timing")

    @staticmethod
    def normalize_email(email: str) -> str:
        e = email.strip().lower()
        if len(e) > 254 or not _EMAIL.match(e):
            raise AuthError("invalid_email", "Invalid e-mail address")
        return e

    def register(self, email: str, password: str):
        e = self.normalize_email(email)
        problems = security.check_password_policy(password)
        if problems:
            raise AuthError("weak_password", "; ".join(problems))
        status = "active" if self.dev_auto_verify else "pending_verification"
        user = self.repo.create_user(e, security.hash_password(password), status)
        if user is None:
            raise AuthError("email_taken", "This e-mail is already registered", 409)
        return user

    def _issue(self, user, family_id=None) -> TokenPair:
        raw = security.new_refresh_token()
        self.repo.add_refresh(RefreshRecord(
            str(uuid.uuid4()), user.id, family_id or str(uuid.uuid4()),
            security.hash_token(raw), self.clock() + self.refresh_ttl))
        return TokenPair(security.make_access_token(user.id, user.roles, self.secret, self.access_ttl),
                         raw, self.access_ttl)

    def login(self, email: str, password: str) -> TokenPair:
        try:
            e = self.normalize_email(email)
        except AuthError:
            security.verify_password(password, self._dummy)
            raise AuthError("invalid_credentials", "Wrong e-mail or password", 401)
        user = self.repo.get_user_by_email(e)
        ok = security.verify_password(password, user.password_hash if user else self._dummy)
        if not user or not ok:
            raise AuthError("invalid_credentials", "Wrong e-mail or password", 401)
        if user.status == "blocked":
            raise AuthError("account_blocked", "Account is blocked", 403)
        if user.status != "active":
            raise AuthError("not_verified", "E-mail is not verified", 403)
        return self._issue(user)

    def refresh(self, token: str) -> TokenPair:
        rec = self.repo.get_refresh(security.hash_token(token))
        if rec is None or rec.expires_at < self.clock():
            raise AuthError("invalid_refresh", "Invalid refresh token", 401)
        if rec.revoked:
            # An old token was presented again: assume theft, kill the whole family.
            self.repo.revoke_family(rec.family_id)
            raise AuthError("invalid_refresh", "Invalid refresh token", 401)
        user = self.repo.get_user(rec.user_id)
        if not user or user.status != "active":
            raise AuthError("invalid_refresh", "Invalid refresh token", 401)
        self.repo.revoke_refresh(rec.token_hash)
        return self._issue(user, rec.family_id)
