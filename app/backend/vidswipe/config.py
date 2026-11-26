"""Application settings, read from environment variables (see .env.example)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    data_dir: Path = Path("/data")
    media_dir: Path = Path("/media")
    static_dir: Path = Path("/srv/static")
    database_url: str = ""

    admin_password: str = ""
    session_secret: str = "change-me"
    session_days: int = 30
    cookie_secure: bool = True

    ingest_api_key: str = ""
    importer_url: str = "http://importer:8081"
    importer_api_key: str = ""

    login_rate_limit: int = 10  # attempts per window per IP
    login_rate_window_s: int = 300

    # Feed weights (DESIGN.md 5.5)
    feed_k: float = 5.0
    feed_w_tag: float = 1.0
    feed_w_creator: float = 0.8
    feed_w_source: float = 0.2
    feed_w_fresh: float = 0.2
    feed_w_random: float = 0.5
    feed_fresh_days: float = 30.0
    feed_explore_every: int = 5
    feed_candidates: int = 500

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'app.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
