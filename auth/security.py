"""
auth/security.py

The cryptographic pieces of authentication, with no FastAPI in them:
password hashing (Argon2id through pwdlib), the password rules, session
tokens (HS256 JWTs through PyJWT) and API keys (random, stored as SHA-256).

Switching to an identity server later (docs/AUTH.md §7) replaces
issue_session_token / decode_session_token: decoding would check the
server's published keys (jwt.PyJWKClient) instead of SESSION_SECRET.
"""

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timezone

import jwt
from pwdlib import PasswordHash

from auth import config

# --- Passwords (NIST SP 800-63B-4, password as the only factor) ---

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 256  # long passphrases are welcome; the cap only bounds the hashing work
MIN_DISTINCT_CHARACTERS = 5

_hasher = PasswordHash.recommended()  # Argon2id
# Verified against when the email is unknown, so both cases take the same time
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> tuple[bool, str | None]:
    """(matches, new hash or None). A new hash is returned when the stored one uses
    outdated parameters. With no stored hash, a dummy one is checked and False returned."""
    if not password_hash:
        _hasher.verify(password, _DUMMY_HASH)
        return False, None
    return _hasher.verify_and_update(password, password_hash)


def password_problem(password: str, email: str) -> str | None:
    """Why the password is refused, or None. No composition rules (no forced
    digits or symbols): length, and nothing guessable from the account."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"The password must be at least {MIN_PASSWORD_LENGTH} characters long"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"The password must be at most {MAX_PASSWORD_LENGTH} characters long"
    if len(set(password)) < MIN_DISTINCT_CHARACTERS:
        return "The password is too repetitive"
    lowered = password.lower()
    if email and (email.lower() in lowered or email.split("@")[0].lower() == lowered):
        return "The password must not contain the email address"
    return None


# --- Session tokens ---

ALGORITHM = "HS256"
ISSUER = "rada"
AUDIENCE = "rada-api"


def issue_session_token(user_id: int, role: str) -> tuple[str, datetime]:
    """A signed session token and its expiry. iat keeps its fraction of a second,
    so a logout ends sessions issued in the same second but not the next login."""
    now = time.time()
    expires = now + config.settings.session_ttl_seconds
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        "role": role,  # informative only: the API reads the role from the database
        "iat": now,
        "exp": expires,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(claims, config.settings.session_secret, algorithm=ALGORITHM)
    return token, datetime.fromtimestamp(expires, tz=timezone.utc)


def decode_session_token(token: str) -> dict:
    """The token's claims. Raises jwt.InvalidTokenError when it is not valid."""
    return jwt.decode(
        token,
        config.settings.session_secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "sub", "iss", "aud"]},
    )


# --- API keys ---

API_KEY_PREFIX = "rada_"
DISPLAY_PREFIX_LENGTH = 12  # "rada_" + 7 characters


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    """SHA-256 is enough here: the key is 256 random bits, nothing to brute-force,
    and a fast hash can be looked up by index on every request."""
    return hashlib.sha256(key.encode()).hexdigest()


def display_prefix(key: str) -> str:
    return key[:DISPLAY_PREFIX_LENGTH]


def same_secret(given: str | None, expected: str | None) -> bool:
    """Constant-time comparison; False when either side is missing."""
    if not given or not expected:
        return False
    return secrets.compare_digest(given.encode(), expected.encode())
