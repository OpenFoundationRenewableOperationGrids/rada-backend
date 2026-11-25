"""
auth/config.py

Authentication settings, read from the environment once at import
(docs/AUTH.md §6). Code reads them as `config.settings.<name>` at call time,
so tests can change one with monkeypatch.

No secret has a default: with AUTH_ENABLED=true the API refuses to start
without SESSION_SECRET, and API_KEY (the MVP's single key) is only accepted
when it is set.
"""

import logging
import os
import secrets
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()  # main.py imports this before database.py, which also loads .env

log = logging.getLogger("rada.auth")

SESSION_COOKIE = "rada_session"
MIN_SECRET_LENGTH = 32


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    auth_enabled: bool
    environment: str
    session_secret: str
    session_ttl_seconds: int
    cookie_secure: bool
    legacy_api_key: str | None  # the MVP's API_KEY; removed in BE-09
    setup_token: str | None
    allowed_origins: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "Settings":
        origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")
        return cls(
            auth_enabled=_flag("AUTH_ENABLED", "true"),
            environment=os.getenv("ENVIRONMENT", "production").strip().lower(),
            session_secret=os.getenv("SESSION_SECRET", "").strip(),
            session_ttl_seconds=int(float(os.getenv("SESSION_TTL_HOURS", "8")) * 3600),
            cookie_secure=_flag("COOKIE_SECURE", "true"),
            legacy_api_key=os.getenv("API_KEY", "").strip() or None,
            setup_token=os.getenv("SETUP_TOKEN", "").strip() or None,
            allowed_origins=[o.strip().rstrip("/") for o in origins.split(",") if o.strip()],
        )

    def validate(self) -> None:
        """Refuse to start with an unsafe configuration."""
        if not self.auth_enabled and self.environment != "development":
            raise RuntimeError(
                f"AUTH_ENABLED=false is only allowed with ENVIRONMENT=development (got {self.environment!r})"
            )
        if len(self.session_secret) < MIN_SECRET_LENGTH:
            if self.auth_enabled:
                raise RuntimeError(
                    f"SESSION_SECRET must be set to at least {MIN_SECRET_LENGTH} characters. Generate one with: "
                    'python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
            # Development without auth: sign-in still works, sessions end at restart
            self.session_secret = secrets.token_urlsafe(48)
            log.warning("SESSION_SECRET is not set: using a random one, sessions end when the API restarts")
        if self.session_ttl_seconds <= 0:
            raise RuntimeError("SESSION_TTL_HOURS must be positive")
        if self.legacy_api_key:
            log.warning("API_KEY (single shared key) is still accepted: remove it once the front end "
                        "and the bridge use named API keys (BE-09)")


settings = Settings.from_env()
