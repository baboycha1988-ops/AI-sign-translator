"""Storage interface for auth data + an in-memory implementation (tests and local dev).
The PostgreSQL implementation follows db/migrations/0001_initial.sql and is the next step."""
import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class User:
    id: str
    email: str
    password_hash: str
    status: str = "pending_verification"
    roles: list = field(default_factory=lambda: ["user"])


@dataclass
class RefreshRecord:
    id: str
    user_id: str
    family_id: str
    token_hash: str
    expires_at: float
    revoked: bool = False


class InMemoryRepo:
    def __init__(self):
        self.users, self.by_email, self.refresh = {}, {}, {}

    def create_user(self, email: str, password_hash: str, status: str) -> Optional[User]:
        if email in self.by_email:
            return None
        u = User(str(uuid.uuid4()), email, password_hash, status)
        self.users[u.id], self.by_email[email] = u, u.id
        return u

    def get_user_by_email(self, email: str) -> Optional[User]:
        uid = self.by_email.get(email)
        return self.users.get(uid) if uid else None

    def get_user(self, user_id: str) -> Optional[User]:
        return self.users.get(user_id)

    def add_refresh(self, rec: RefreshRecord) -> None:
        self.refresh[rec.token_hash] = rec

    def get_refresh(self, token_hash: str) -> Optional[RefreshRecord]:
        return self.refresh.get(token_hash)

    def revoke_refresh(self, token_hash: str) -> None:
        if token_hash in self.refresh:
            self.refresh[token_hash].revoked = True

    def revoke_family(self, family_id: str) -> None:
        for r in self.refresh.values():
            if r.family_id == family_id:
                r.revoked = True
