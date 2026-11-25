"""
auth/deps.py

Who is calling, and what may they do (docs/AUTH.md §3). Every protected
route declares one of these dependencies:

- require_read:   GET endpoints and other reads. Users (admin or viewer),
                  API keys with scope export or admin.
- require_admin:  configuration and writes. Users with the admin role,
                  API keys with scope admin.
- require_ingest: posting measurements and telemetry. API keys with scope
                  ingest or admin, and administrators.
- require_admin_user: managing users and API keys. A signed-in administrator
                  only; no API key may create keys or accounts.

Credentials, checked in this order: the X-API-Key header (a named key, or
the MVP's single API_KEY until BE-09), then an `Authorization: Bearer`
session token (native apps), then the rada_session cookie (web).

With AUTH_ENABLED=false (development only, see config.py) every request is
an administrator.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from auth import config
from auth.security import decode_session_token, hash_api_key, same_secret
from database import get_db
from models import ApiKey, ApiKeyScope, AppUser, UserRole

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LAST_USED_RESOLUTION = timedelta(minutes=1)  # at most one write per key and minute

api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False, scheme_name="ApiKey",
    description="A named API key (machines: the bridge, exports).",
)
bearer_token = HTTPBearer(
    auto_error=False, scheme_name="SessionToken",
    description="A session token from POST /auth/token (native apps).",
)
session_cookie = APIKeyCookie(
    name=config.SESSION_COOKIE, auto_error=False, scheme_name="SessionCookie",
    description="The cookie set by POST /auth/login (web).",
)


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "api_key", "legacy_api_key", "dev"]
    role: UserRole | None = None        # users
    scope: ApiKeyScope | None = None    # API keys
    user_id: int | None = None
    email: str | None = None
    api_key_id: int | None = None
    api_key_name: str | None = None

    @property
    def is_user(self) -> bool:
        return self.kind in ("user", "dev")

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN or self.scope == ApiKeyScope.ADMIN

    @property
    def can_read(self) -> bool:
        return self.is_user or self.scope in (ApiKeyScope.EXPORT, ApiKeyScope.ADMIN)

    @property
    def can_ingest(self) -> bool:
        return self.is_admin or self.scope == ApiKeyScope.INGEST


DEV_PRINCIPAL = Principal(kind="dev", role=UserRole.ADMIN, user_id=0, email="dev@localhost")
LEGACY_PRINCIPAL = Principal(kind="legacy_api_key", scope=ApiKeyScope.ADMIN, api_key_name="API_KEY")


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def _api_key_principal(db: Session, key: str) -> Principal:
    if same_secret(key, config.settings.legacy_api_key):
        return LEGACY_PRINCIPAL
    row = db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(key), ApiKey.revoked_at.is_(None)).one_or_none()
    if row is None:
        raise unauthorized("Invalid or revoked API key")
    now = datetime.now(timezone.utc)
    if row.last_used_at is None or now - row.last_used_at > LAST_USED_RESOLUTION:
        row.last_used_at = now
        db.commit()
    return Principal(kind="api_key", scope=row.scope, api_key_id=row.id, api_key_name=row.name)


def user_from_token(db: Session, token: str) -> AppUser:
    """The active user a session token belongs to. Raises 401 otherwise."""
    try:
        claims = decode_session_token(token)
        user_id = int(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise unauthorized("Session expired or invalid")
    user = db.get(AppUser, user_id)
    if user is None or not user.is_active:
        raise unauthorized("Session expired or invalid")
    if user.sessions_valid_after and claims["iat"] < user.sessions_valid_after.timestamp():
        raise unauthorized("Session expired or invalid")
    return user


def get_principal(
    request: Request,
    db: Session = Depends(get_db),
    api_key: str | None = Security(api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_token),
    cookie: str | None = Security(session_cookie),
) -> Principal:
    if not config.settings.auth_enabled:
        return DEV_PRINCIPAL

    if api_key:
        return _api_key_principal(db, api_key)

    token = bearer.credentials if bearer else cookie
    if not token:
        raise unauthorized("Not authenticated")

    # A cookie is sent by the browser on its own: refuse a write sent from a
    # page of another origin (CSRF). The front end's server-side proxy sends no
    # Origin header, or its own, which is in CORS_ORIGINS.
    if not bearer and request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in config.settings.allowed_origins:
            raise forbidden("Cross-origin request refused")

    user = user_from_token(db, token)
    return Principal(kind="user", role=user.role, user_id=user.id, email=user.email)


def require_read(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.can_read:
        raise forbidden("This API key's scope does not allow reading data")
    return principal


def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_admin:
        raise forbidden("Administrator role required")
    return principal


def require_ingest(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.can_ingest:
        raise forbidden("An API key with the ingest scope, or an administrator, is required")
    return principal


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.is_user:
        raise forbidden("This endpoint needs a signed-in user, not an API key")
    return principal


def require_admin_user(principal: Principal = Depends(require_user)) -> Principal:
    if not principal.is_admin:
        raise forbidden("Administrator role required")
    return principal
