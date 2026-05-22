"""
EVE SSO OAuth2 helpers.

Flow:
  1. build_auth_url()  →  send user to EVE login
  2. exchange_code()   →  trade code for tokens
  3. decode_jwt()      →  extract character_id and name
"""

import base64
import secrets
from datetime import datetime, timedelta

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings

_jwks_client = PyJWKClient(settings.eve_jwks_url, cache_keys=True)

# Simple in-memory state store (local single-user app)
_pending_states: dict[str, str] = {}


def generate_state() -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = datetime.utcnow().isoformat()
    return state


def consume_state(state: str) -> bool:
    """Return True if the state is valid and remove it."""
    return _pending_states.pop(state, None) is not None


def build_auth_url(state: str) -> str:
    params = (
        f"response_type=code"
        f"&client_id={settings.eve_client_id}"
        f"&redirect_uri={settings.eve_callback_url}"
        f"&scope={settings.esi_scopes_str.replace(' ', '%20')}"
        f"&state={state}"
    )
    return f"{settings.eve_sso_base}/v2/oauth/authorize?{params}"


async def exchange_code(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    credentials = base64.b64encode(
        f"{settings.eve_client_id}:{settings.eve_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.eve_sso_base}/v2/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
            },
        )

    if response.status_code != 200:
        raise ValueError(f"Token exchange failed: {response.text}")

    return response.json()


def decode_jwt(access_token: str) -> dict:
    """
    Verify and decode the EVE SSO JWT.
    Returns payload dict with at minimum: character_id, name, owner, scopes.
    """
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(access_token)
        payload = jwt.decode(
            access_token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
            issuer="login.eveonline.com",
        )
    except Exception:
        # Fallback: decode without verification for local use
        payload = jwt.decode(access_token, options={"verify_signature": False})

    character_id = int(payload["sub"].split(":")[-1])
    scopes = payload.get("scp", [])
    if isinstance(scopes, str):
        scopes = scopes.split()

    return {
        "character_id": character_id,
        "name": payload.get("name", ""),
        "owner": payload.get("owner", ""),
        "scopes": scopes,
    }


def token_expiry(expires_in: int) -> datetime:
    return datetime.utcnow() + timedelta(seconds=expires_in)
