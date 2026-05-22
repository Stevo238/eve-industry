from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    eve_client_id: str = ""
    eve_client_secret: str = ""
    eve_callback_url: str = "http://localhost:9555/auth/callback"
    secret_key: str = "change-me"
    database_url: str = "sqlite+aiosqlite:///./eve_industry.db"

    esi_base_url: str = "https://esi.evetech.net/latest"
    eve_sso_base: str = "https://login.eveonline.com"
    eve_jwks_url: str = "https://login.eveonline.com/oauth/jwks"
    eve_sde_url: str = (
        "https://eve-static-data-export.s3-eu-west-1.amazonaws.com/tranquility/sde.zip"
    )

    # Jita / The Forge region for market data
    market_region_id: int = 10000002
    jita_station_id: int = 60003760

    @property
    def esi_scopes(self) -> list[str]:
        return [
            "esi-assets.read_assets.v1",
            "esi-characters.read_blueprints.v1",
            "esi-skills.read_skills.v1",
            "esi-industry.read_character_jobs.v1",
            "esi-markets.read_character_orders.v1",
            "esi-wallet.read_character_wallet.v1",
            "esi-location.read_location.v1",
            "esi-universe.read_structures.v1",
        ]

    @property
    def esi_scopes_str(self) -> str:
        return " ".join(self.esi_scopes)


settings = Settings()
