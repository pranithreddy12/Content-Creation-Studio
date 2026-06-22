"""Crypto helpers — JWT verification + AES-GCM for stored OAuth tokens."""
from __future__ import annotations

import base64
import os
from functools import lru_cache
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings


@lru_cache(maxsize=1)
def _jwks() -> dict[str, Any]:
    if not settings.clerk_jwks_url:
        return {"keys": []}
    return httpx.get(settings.clerk_jwks_url, timeout=10).json()


def verify_clerk_jwt(token: str) -> dict[str, Any]:
    jwks = _jwks()
    unverified = jwt.get_unverified_header(token)
    kid = unverified.get("kid")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise jwt.InvalidTokenError("kid not found")
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
    return jwt.decode(
        token,
        key=public_key,
        algorithms=["RS256"],
        # PyJWT treats `issuer=""` as a required check and rejects everything; coerce blank → None.
        issuer=settings.clerk_jwt_issuer or None,
        options={"verify_aud": False},
    )


def _master_key() -> bytes:
    raw = settings.secret_key.encode("utf-8").ljust(32, b"0")[:32]
    return raw


def encrypt(plaintext: str | bytes) -> str:
    if isinstance(plaintext, str):
        plaintext = plaintext.encode("utf-8")
    nonce = os.urandom(12)
    ct = AESGCM(_master_key()).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str) -> bytes:
    raw = base64.b64decode(token.encode("ascii"))
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_master_key()).decrypt(nonce, ct, None)
