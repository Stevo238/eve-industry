from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.esi.auth import (
    build_auth_url,
    consume_state,
    decode_jwt,
    exchange_code,
    generate_state,
    token_expiry,
)
from app.models.character import Character, OAuthToken

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login():
    state = generate_state()
    return RedirectResponse(build_auth_url(state))


@router.get("/callback")
async def callback(code: str, state: str, db: AsyncSession = Depends(get_db)):
    if not consume_state(state):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    token_data = await exchange_code(code)
    jwt_info = decode_jwt(token_data["access_token"])

    character_id = jwt_info["character_id"]

    # Upsert Character
    char = await db.get(Character, character_id)
    if char is None:
        char = Character(
            character_id=character_id,
            name=jwt_info["name"],
            owner_hash=jwt_info["owner"],
        )
        db.add(char)
    else:
        char.name = jwt_info["name"]
        char.owner_hash = jwt_info["owner"]

    # Upsert OAuthToken (one per character)
    token = await db.get(OAuthToken, character_id)
    if token is None:
        token = OAuthToken(character_id=character_id)
        db.add(token)

    token.access_token = token_data["access_token"]
    token.refresh_token = token_data["refresh_token"]
    token.expires_at = token_expiry(token_data["expires_in"])
    token.scopes = " ".join(jwt_info["scopes"])

    await db.commit()

    return RedirectResponse(f"/characters?added={character_id}")


@router.get("/remove/{character_id}")
async def remove_character(character_id: int, db: AsyncSession = Depends(get_db)):
    char = await db.get(Character, character_id)
    token = await db.get(OAuthToken, character_id)
    if token:
        await db.delete(token)
    if char:
        await db.delete(char)
    await db.commit()
    return RedirectResponse("/characters")
