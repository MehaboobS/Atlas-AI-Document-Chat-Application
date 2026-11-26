"""
Atlas AI - JWT Authentication & Password Security
Provides scrypt password hashing/verification and stateless HS256 JWT tokens
with strict per-user tenant isolation (each user gets their own private workspace).
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from app.config import settings

security = HTTPBearer(auto_error=False)


class AuthUser(BaseModel):
    user_id: str
    tenant_id: str
    email: str
    full_name: str = ""


def hash_password(password: str) -> str:
    """Hash a plaintext password using memory-hard scrypt with a 16-byte random salt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify plaintext password against stored scrypt hash using constant-time comparison."""
    try:
        scheme, salt_hex, hash_hex = stored_hash.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            dklen=32,
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_access_token(user_id: str, email: str, full_name: str = "") -> str:
    """Create a signed HS256 JWT access token for the authenticated user."""
    cfg = settings()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=cfg.jwt_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "email": email,
        "name": full_name,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, cfg.jwt_secret_key, algorithm=cfg.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify and decode a JWT access token, raising 401 if invalid or expired."""
    cfg = settings()
    try:
        return jwt.decode(
            token,
            cfg.jwt_secret_key,
            algorithms=[cfg.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> AuthUser:
    """FastAPI dependency extracting and validating the current user from Bearer JWT."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    claims = decode_access_token(credentials.credentials)
    user_id = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip()
    full_name = str(claims.get("name", "")).strip()

    if not user_id or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Strict per-user isolation: each user acts inside their own private tenant namespace
    return AuthUser(
        user_id=user_id,
        tenant_id=user_id,
        email=email,
        full_name=full_name,
    )
