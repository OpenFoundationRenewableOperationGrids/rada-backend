"""
auth/routes.py

The sign-in part of the API contract (C1, docs/AUTH.md §2):

- /auth: installation state, first administrator, sign-in (cookie for the
  web, token in the body for native apps), sign-out, "who am I", password
  change.
- /users: accounts, managed by a signed-in administrator.
- /api-keys: named machine keys, managed by a signed-in administrator. A key
  is shown once, when it is created.
"""

import math
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import config, throttle
from auth.deps import (
    Principal, bearer_token, require_admin_user, require_user, session_cookie, unauthorized, user_from_token,
)
from auth.security import (
    display_prefix, generate_api_key, hash_api_key, hash_password, issue_session_token, password_problem,
    same_secret, verify_password,
)
from database import get_db
from models import ApiKey, ApiKeyScope, AppUser, AuthMethod, UserRole

# Serialises POST /auth/setup, so two first administrators cannot be created at once
SETUP_LOCK_ID = 7_340_404
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+$")
INVALID_CREDENTIALS = "Invalid email or password"


# --- Schemas ---

class EmailField(BaseModel):
    email: str = Field(..., max_length=254, examples=["name@organisation.org"])

    @field_validator("email")
    @classmethod
    def normalise_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.match(value):
            raise ValueError("Not an email address")
        return value


class Credentials(EmailField):
    password: str = Field(..., min_length=1, max_length=1024)


class SetupRequest(EmailField):
    password: str = Field(..., max_length=1024, description="At least 15 characters; a passphrase works best.")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    role: UserRole
    auth_method: AuthMethod
    is_active: bool
    last_login_at: Optional[datetime]
    created_at: Optional[datetime]


class SessionOut(BaseModel):
    user: UserOut
    expires_at: datetime


class TokenOut(SessionOut):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Seconds until the token expires.")


class SetupStatus(BaseModel):
    needs_setup: bool = Field(..., description="True while no user exists: show the first-run screen.")
    setup_token_required: bool = Field(..., description="POST /auth/setup needs the X-Setup-Token header.")


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., max_length=1024)


class UserCreate(EmailField):
    password: str = Field(..., max_length=1024)
    role: UserRole = UserRole.VIEWER


class UserPatch(BaseModel):
    role: Optional[UserRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, max_length=1024, description="Sets a new password and ends the user's sessions.")


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
                      examples=["bridge-openems"])
    scope: ApiKeyScope


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    scope: ApiKeyScope
    created_by_id: Optional[int]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(..., description="The key itself. It is shown only in this response.")


# --- Helpers ---

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_password(password: str, email: str) -> None:
    problem = password_problem(password, email)
    if problem:
        raise HTTPException(status_code=422, detail=problem)


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        config.SESSION_COOKIE, token,
        max_age=config.settings.session_ttl_seconds,
        httponly=True, secure=config.settings.cookie_secure, samesite="lax", path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        config.SESSION_COOKIE, httponly=True, secure=config.settings.cookie_secure, samesite="lax", path="/",
    )


def _authenticate(db: Session, request: Request, credentials: Credentials) -> AppUser:
    """The user these credentials sign in, or 401/429. The same message for an
    unknown email, a wrong password and a deactivated account."""
    keys = ((throttle.by_email, f"email:{credentials.email}"), (throttle.by_client, _client_address(request)))
    wait = max(limiter.retry_after(key) for limiter, key in keys)
    if wait > 0:
        raise HTTPException(
            status_code=429, detail="Too many failed sign-in attempts. Try again later.",
            headers={"Retry-After": str(math.ceil(wait))},
        )

    user = db.query(AppUser).filter(AppUser.email == credentials.email).one_or_none()
    usable = user is not None and user.is_active and user.auth_method == AuthMethod.LOCAL
    ok, new_hash = verify_password(credentials.password, user.password_hash if usable else None)
    if not ok:
        for limiter, key in keys:
            limiter.record_failure(key)
        raise unauthorized(INVALID_CREDENTIALS)

    throttle.by_email.record_success(keys[0][1])
    if new_hash:
        user.password_hash = new_hash
    user.last_login_at = _now()
    db.commit()
    db.refresh(user)
    return user


def _active_admin_count(db: Session) -> int:
    return db.query(func.count(AppUser.id)).filter(AppUser.role == UserRole.ADMIN, AppUser.is_active).scalar()


def _violates(error: IntegrityError, constraint: str) -> bool:
    diag = getattr(error.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == constraint


# --- /auth ---

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.get(
    "/setup-status", response_model=SetupStatus,
    summary="Installation state",
    description="Unauthenticated. `needs_setup` is true on a new installation with no user: "
                "the front end shows the first-run screen, which calls POST /auth/setup.",
)
def setup_status(db: Session = Depends(get_db)):
    return {
        "needs_setup": db.query(AppUser.id).first() is None,
        "setup_token_required": config.settings.setup_token is not None,
    }


@router.post(
    "/setup", status_code=201, response_model=SessionOut,
    summary="Create the first administrator",
    description="Works once, while no user exists, and signs the new administrator in (cookie). "
                "When SETUP_TOKEN is configured, the X-Setup-Token header must match it.",
    responses={
        403: {"description": "Missing or wrong X-Setup-Token"},
        409: {"description": "RADA is already set up"},
        422: {"description": "Invalid email, or the password breaks a rule"},
    },
)
def setup(
    body: SetupRequest, response: Response, db: Session = Depends(get_db),
    x_setup_token: Optional[str] = Header(None),
):
    if config.settings.setup_token and not same_secret(x_setup_token, config.settings.setup_token):
        raise HTTPException(status_code=403, detail="Missing or wrong setup token")
    _check_password(body.password, body.email)

    db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": SETUP_LOCK_ID})
    if db.query(AppUser.id).first() is not None:
        raise HTTPException(status_code=409, detail="RADA is already set up")
    user = AppUser(email=body.email, password_hash=hash_password(body.password), role=UserRole.ADMIN,
                   auth_method=AuthMethod.LOCAL, last_login_at=_now())
    db.add(user)
    db.commit()
    db.refresh(user)

    token, expires_at = issue_session_token(user.id, user.role.value)
    _set_session_cookie(response, token)
    return {"user": user, "expires_at": expires_at}


@router.post(
    "/login", response_model=SessionOut,
    summary="Sign in (web)",
    description="Sets the session in the httpOnly `rada_session` cookie. Repeated failures are slowed down (429).",
    responses={
        401: {"description": "Invalid email or password (same message for both)"},
        429: {"description": "Too many failed attempts; see the Retry-After header"},
    },
)
def login(credentials: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    user = _authenticate(db, request, credentials)
    token, expires_at = issue_session_token(user.id, user.role.value)
    _set_session_cookie(response, token)
    return {"user": user, "expires_at": expires_at}


@router.post(
    "/token", response_model=TokenOut,
    summary="Sign in (native apps)",
    description="Same accounts and rules as /auth/login, but returns the session token in the body "
                "and sets no cookie. Send it as `Authorization: Bearer <token>`.",
    responses={
        401: {"description": "Invalid email or password (same message for both)"},
        429: {"description": "Too many failed attempts; see the Retry-After header"},
    },
)
def token(credentials: Credentials, request: Request, db: Session = Depends(get_db)):
    user = _authenticate(db, request, credentials)
    access_token, expires_at = issue_session_token(user.id, user.role.value)
    return {
        "user": user, "expires_at": expires_at, "access_token": access_token,
        "expires_in": config.settings.session_ttl_seconds,
    }


@router.post(
    "/logout", status_code=204,
    summary="Sign out",
    description="Ends every session of the user (web and apps) and clears the cookie. "
                "Answers 204 even without a valid session.",
)
def logout(
    response: Response, db: Session = Depends(get_db),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(bearer_token),
    cookie: Optional[str] = Depends(session_cookie),
):
    session = bearer.credentials if bearer else cookie
    if session:
        try:
            user = user_from_token(db, session)
        except HTTPException:
            user = None
        if user is not None:
            user.sessions_valid_after = _now()
            db.commit()
    _clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get(
    "/me", response_model=UserOut,
    summary="Who am I",
    description="The signed-in user. 401 without a valid session; 403 for an API key.",
)
def me(principal: Principal = Depends(require_user), db: Session = Depends(get_db)):
    if principal.kind == "dev":  # AUTH_ENABLED=false
        return UserOut(id=0, email=principal.email, role=UserRole.ADMIN, auth_method=AuthMethod.LOCAL,
                       is_active=True, last_login_at=None, created_at=None)
    return db.get(AppUser, principal.user_id)


@router.post(
    "/password", status_code=204,
    summary="Change my password",
    description="Ends every session of the user, this one included: sign in again with the new password.",
    responses={401: {"description": "Wrong current password"}, 422: {"description": "The new password breaks a rule"}},
)
def change_password(
    body: PasswordChange, response: Response,
    principal: Principal = Depends(require_user), db: Session = Depends(get_db),
):
    user = db.get(AppUser, principal.user_id) if principal.kind == "user" else None
    if user is None or user.auth_method != AuthMethod.LOCAL:
        raise HTTPException(status_code=403, detail="This account has no RADA password")
    ok, _ = verify_password(body.current_password, user.password_hash)
    if not ok:
        raise unauthorized("Wrong current password")
    _check_password(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    user.sessions_valid_after = _now()
    db.commit()
    _clear_session_cookie(response)
    response.status_code = 204
    return response


# --- /users ---

users_router = APIRouter(prefix="/users", tags=["Users"], dependencies=[Depends(require_admin_user)])


@users_router.get("", response_model=list[UserOut], summary="List users")
def list_users(db: Session = Depends(get_db)):
    return db.query(AppUser).order_by(AppUser.id).all()


@users_router.post(
    "", status_code=201, response_model=UserOut,
    summary="Create a user",
    description="Creates a local account, viewer by default. Give the person their password another way.",
    responses={409: {"description": "A user with this email already exists"},
               422: {"description": "Invalid email, or the password breaks a rule"}},
)
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    _check_password(body.password, body.email)
    user = AppUser(email=body.email, password_hash=hash_password(body.password), role=body.role,
                   auth_method=AuthMethod.LOCAL)
    db.add(user)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if not _violates(error, "app_user_email_key"):
            raise
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    db.refresh(user)
    return user


@users_router.patch(
    "/{user_id}", response_model=UserOut,
    summary="Change a user's role, status or password",
    description="Deactivating a user or setting their password ends their sessions. "
                "The last active administrator cannot be demoted or deactivated.",
    responses={404: {"description": "User not found"},
               409: {"description": "At least one active administrator must remain"},
               422: {"description": "The password breaks a rule"}},
)
def update_user(user_id: int, body: UserPatch, db: Session = Depends(get_db)):
    # Locks the row, so two administrators demoting each other cannot both succeed
    user = db.query(AppUser).filter(AppUser.id == user_id).with_for_update().one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail=f"User {user_id} not found")
    updates = body.model_dump(exclude_unset=True)
    if any(updates.get(f, "") is None for f in ("role", "is_active", "password")):
        raise HTTPException(status_code=422, detail="Fields cannot be null")

    loses_admin = user.role == UserRole.ADMIN and user.is_active and (
        updates.get("role", UserRole.ADMIN) != UserRole.ADMIN or updates.get("is_active") is False
    )
    if loses_admin:
        db.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": SETUP_LOCK_ID})
        if _active_admin_count(db) <= 1:
            raise HTTPException(status_code=409, detail="At least one active administrator must remain")

    if "password" in updates:
        if user.auth_method != AuthMethod.LOCAL:
            raise HTTPException(status_code=422, detail="This account signs in through SSO and has no RADA password")
        _check_password(updates["password"], user.email)
        user.password_hash = hash_password(updates["password"])
        user.sessions_valid_after = _now()
    if "role" in updates:
        user.role = updates["role"]
    if "is_active" in updates:
        if user.is_active and not updates["is_active"]:
            user.sessions_valid_after = _now()
        user.is_active = updates["is_active"]
    db.commit()
    db.refresh(user)
    return user


# --- /api-keys ---

api_keys_router = APIRouter(prefix="/api-keys", tags=["API keys"])


@api_keys_router.get("", response_model=list[ApiKeyOut], summary="List API keys",
                     dependencies=[Depends(require_admin_user)])
def list_api_keys(db: Session = Depends(get_db)):
    return db.query(ApiKey).order_by(ApiKey.id).all()


@api_keys_router.post(
    "", status_code=201, response_model=ApiKeyCreated,
    summary="Create an API key",
    description="Scopes: `ingest` posts measurements and telemetry (the bridge), `export` reads data, "
                "`admin` does what an administrator may do, except managing users and keys. "
                "The key is in this response only; RADA stores its hash.",
    responses={409: {"description": "A key with this name already exists"}},
)
def create_api_key(body: ApiKeyCreate, principal: Principal = Depends(require_admin_user),
                   db: Session = Depends(get_db)):
    key = generate_api_key()
    row = ApiKey(name=body.name, prefix=display_prefix(key), key_hash=hash_api_key(key), scope=body.scope,
                 created_by_id=principal.user_id or None)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if not _violates(error, "api_key_name_key"):
            raise
        raise HTTPException(status_code=409, detail=f"An API key named {body.name} already exists")
    db.refresh(row)
    return {**ApiKeyOut.model_validate(row).model_dump(), "key": key}


@api_keys_router.delete(
    "/{key_id}", status_code=204,
    summary="Revoke an API key",
    description="The key is refused from its next request. The row is kept, with revoked_at. Revoking twice is not an error.",
    responses={404: {"description": "API key not found"}},
    dependencies=[Depends(require_admin_user)],
)
def revoke_api_key(key_id: int, db: Session = Depends(get_db)):
    row = db.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"API key {key_id} not found")
    if row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()
    return Response(status_code=204)
