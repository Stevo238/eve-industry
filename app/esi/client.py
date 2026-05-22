"""
Async ESI HTTP client with automatic token refresh and pagination support.
All authenticated requests require a character_id with a stored OAuthToken.
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.character import OAuthToken

USER_AGENT = "EVE-Industry-Manager/1.0 (contact: local-app)"


class ESIError(Exception):
    pass


class ESIClient:
    def __init__(self, db: AsyncSession):
        self.db = db
        self._http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=30.0,
        )

    async def close(self):
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _load_token(self, character_id: int) -> OAuthToken:
        result = await self.db.execute(
            select(OAuthToken).where(OAuthToken.character_id == character_id)
        )
        token = result.scalar_one_or_none()
        if token is None:
            raise ESIError(f"No OAuth token found for character {character_id}")
        return token

    async def _ensure_fresh(self, character_id: int) -> str:
        """Return a valid access token, refreshing if needed."""
        token = await self._load_token(character_id)

        if datetime.utcnow() >= token.expires_at - timedelta(minutes=5):
            token = await self._refresh(token)

        return token.access_token

    async def _refresh(self, token: OAuthToken) -> OAuthToken:
        response = await self._http.post(
            f"{settings.eve_sso_base}/v2/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": token.refresh_token},
            auth=(settings.eve_client_id, settings.eve_client_secret),
        )
        if response.status_code != 200:
            raise ESIError(f"Token refresh failed: {response.text}")

        data = response.json()
        token.access_token = data["access_token"]
        token.refresh_token = data["refresh_token"]
        token.expires_at = datetime.utcnow() + timedelta(seconds=data["expires_in"])
        await self.db.commit()
        return token

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    async def get(
        self,
        path: str,
        character_id: int | None = None,
        params: dict | None = None,
    ) -> dict | list:
        url = f"{settings.esi_base_url}{path}"
        headers = {}
        if character_id is not None:
            headers["Authorization"] = f"Bearer {await self._ensure_fresh(character_id)}"

        response = await self._http.get(url, headers=headers, params=params or {})
        self._check_response(response)
        return response.json()

    async def get_paginated(
        self,
        path: str,
        character_id: int | None = None,
        params: dict | None = None,
    ) -> list:
        """Fetch all pages of a paginated ESI endpoint."""
        params = dict(params or {})
        all_items: list = []
        page = 1

        while True:
            params["page"] = page
            url = f"{settings.esi_base_url}{path}"
            headers = {}
            if character_id is not None:
                headers["Authorization"] = (
                    f"Bearer {await self._ensure_fresh(character_id)}"
                )

            response = await self._http.get(url, headers=headers, params=params)
            self._check_response(response)

            data = response.json()
            if isinstance(data, list):
                all_items.extend(data)
            else:
                all_items.append(data)

            total_pages = int(response.headers.get("X-Pages", 1))
            if page >= total_pages:
                break
            page += 1

            # Be polite to ESI
            await asyncio.sleep(0.1)

        return all_items

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        if response.status_code == 204:
            return
        raise ESIError(
            f"ESI {response.request.url} returned {response.status_code}: {response.text[:200]}"
        )
