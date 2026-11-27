"""FastAPI application factory. Run:  JWT_SECRET=<32+ chars> uvicorn app.main:app

NOTE: written without the ability to execute FastAPI in the authoring environment;
the domain logic it calls is covered by tests/test_core.py, the HTTP layer by tests/test_api.py."""
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth_service import AuthError, AuthService
from .core import security
from .core.permissions import has_permission
from .core.ratelimit import TokenBucket
from .dictionary_service import Actor, DictRepo, DictionaryService, DomainError, ProposalService
from .repo import InMemoryRepo


class Credentials(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class ProposalIn(BaseModel):
    gloss: str = Field(min_length=1, max_length=120)
    sign_language: str = Field(min_length=2, max_length=40)
    translations: list = Field(min_length=1, max_length=10)   # [["tg", "..."], ["ru", "..."]]
    description: str = Field(default="", max_length=2000)


class ReviewIn(BaseModel):
    decision: str = Field(pattern="^(approve|reject|duplicate)$")
    note: Optional[str] = Field(default=None, max_length=2000)
    duplicate_of: Optional[str] = None


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=200)


def create_app(repo=None, jwt_secret: Optional[str] = None, dev_auto_verify: Optional[bool] = None,
               cors_origins: Optional[list] = None, auth_rate=(10, 10 / 60)) -> FastAPI:
    secret = jwt_secret or os.environ.get("JWT_SECRET", "")
    if dev_auto_verify is None:
        dev_auto_verify = os.environ.get("DEV_AUTO_VERIFY") == "1"
    if cors_origins is None:
        cors_origins = [o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o]
    repo = repo or InMemoryRepo()
    auth = AuthService(repo, secret, dev_auto_verify=dev_auto_verify)   # raises if secret < 32 chars
    limiter = TokenBucket(*auth_rate)

    app = FastAPI(title="AI Sign Translator API", version="0.1.0")
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            resp.headers["Cache-Control"] = "no-store"
        if request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.exception_handler(AuthError)
    async def auth_error(_: Request, exc: AuthError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status)

    def limit_auth(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            raise HTTPException(status_code=429, detail="Too many requests")

    def current_user(authorization: Optional[str] = Header(default=None)):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        try:
            claims = security.decode_access_token(authorization[7:].strip(), secret)
        except security.TokenError:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        user = repo.get_user(claims["sub"])           # roles/status are re-read, never trusted from the token
        if user is None or user.status != "active":
            raise HTTPException(status_code=401, detail="Account is not active")
        return user

    def require(permission: str):
        def dep(user=Depends(current_user)):
            if not has_permission(user.roles, permission):
                raise HTTPException(status_code=403, detail="Permission denied")
            return user
        return dep

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/auth/register", status_code=201, dependencies=[Depends(limit_auth)])
    def register(body: Credentials):
        u = auth.register(body.email, body.password)
        return {"id": u.id, "email": u.email, "status": u.status}

    @app.post("/api/auth/login", dependencies=[Depends(limit_auth)])
    def login(body: Credentials):
        t = auth.login(body.email, body.password)
        return {"access_token": t.access_token, "refresh_token": t.refresh_token,
                "token_type": "bearer", "expires_in": t.expires_in}

    @app.post("/api/auth/refresh", dependencies=[Depends(limit_auth)])
    def refresh(body: RefreshIn):
        t = auth.refresh(body.refresh_token)
        return {"access_token": t.access_token, "refresh_token": t.refresh_token,
                "token_type": "bearer", "expires_in": t.expires_in}

    @app.get("/api/users/me")
    def me(user=Depends(current_user)):
        return {"id": user.id, "email": user.email, "roles": user.roles}

    @app.get("/api/admin/whoami")
    def admin_whoami(user=Depends(require("audit.read"))):
        return {"id": user.id, "roles": user.roles}

    # ---- dictionary and sign proposals (module 20); in-memory until the PostgreSQL repo exists ----
    drepo = DictRepo()
    dictionary, proposals = DictionaryService(drepo), ProposalService(drepo)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.http_status)

    def actor(user):
        return Actor(user.id, user.roles)

    @app.get("/api/dictionary/search")
    def dictionary_search(q: str = "", lang: str = "tg", user=Depends(current_user)):
        return {"results": dictionary.search(q, lang) if q.strip() else []}

    @app.post("/api/dictionary/proposals", status_code=201)
    def propose(body: ProposalIn, user=Depends(current_user)):
        pairs = [(str(x[0]), str(x[1])) for x in body.translations if isinstance(x, (list, tuple)) and len(x) == 2]
        out = proposals.submit(actor(user), gloss=body.gloss, sign_language=body.sign_language,
                               translations=pairs, description=body.description)
        return {"proposal": out["proposal"], "possible_duplicates": out["possible_duplicates"]}

    @app.get("/api/dictionary/proposals/mine")
    def my_proposals(user=Depends(current_user)):
        return {"proposals": proposals.list_own(actor(user))}

    @app.post("/api/admin/proposals/{proposal_id}/review")
    def review(proposal_id: str, body: ReviewIn, user=Depends(require("signs.review"))):
        return proposals.review(actor(user), proposal_id, body.decision, note=body.note, duplicate_of=body.duplicate_of)

    return app


app = create_app() if os.environ.get("JWT_SECRET") else None
