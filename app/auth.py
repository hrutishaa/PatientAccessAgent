"""OAuth2 password flow issuing short-lived JWTs with role-based scopes (synthetic demo users)."""
from __future__ import annotations

import hmac
import time

import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes

from app.config import settings

ROLE_SCOPES = {
    "patient": ["chat", "appointments:self"],
    "staff": ["chat", "patients:read", "appointments:read", "appointments:write", "fhir"],
    "admin": ["chat", "patients:read", "appointments:read", "appointments:write", "fhir", "audit:read"],
}
DEMO_USERS = {  # username -> (role, linked patient id)
    "jordan": ("patient", "pat-001"),
    "frontdesk": ("staff", None),
    "admin": ("admin", None),
}

oauth2 = OAuth2PasswordBearer(tokenUrl="token", scopes={s: s for r in ROLE_SCOPES.values() for s in r})


def authenticate(username: str, password: str) -> dict | None:
    if username not in DEMO_USERS or not hmac.compare_digest(password, settings.demo_password):
        return None
    role, pid = DEMO_USERS[username]
    return {"sub": username, "role": role, "patient_id": pid}


def issue_token(user: dict) -> str:
    now = int(time.time())
    claims = {**user, "scope": " ".join(ROLE_SCOPES[user["role"]]), "iat": now,
              "exp": now + settings.jwt_ttl_minutes * 60, "iss": "patient-access-agent"}
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def current_user(scopes: SecurityScopes, token: str = Depends(oauth2)) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer="patient-access-agent")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"}) from None
    granted = set(claims.get("scope", "").split())
    missing = [s for s in scopes.scopes if s not in granted]
    if missing:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing scope: {' '.join(missing)}")
    return claims


def require(*scopes: str):
    return Security(current_user, scopes=list(scopes))
