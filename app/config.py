"""Settings come from environment variables only (no secrets in code)."""
from __future__ import annotations

import os
import secrets
import warnings
from dataclasses import dataclass, field


def _jwt_secret() -> str:
    value = os.getenv("JWT_SECRET")
    if value:
        return value
    warnings.warn("JWT_SECRET not set; using an ephemeral random secret (dev only).", stacklevel=2)
    return secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Settings:
    jwt_secret: str = field(default_factory=_jwt_secret)
    jwt_ttl_minutes: int = int(os.getenv("JWT_TTL_MINUTES", "30"))
    voice_webhook_secret: str = field(default_factory=lambda: os.getenv("VOICE_WEBHOOK_SECRET", ""))
    demo_password: str = field(default_factory=lambda: os.getenv("DEMO_PASSWORD", "synthetic-demo"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    langsmith_enabled: bool = bool(os.getenv("LANGSMITH_API_KEY"))
    max_failures_before_handoff: int = 2


settings = Settings()
