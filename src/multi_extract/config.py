"""Settings, from ME_-prefixed environment variables or a .env file.

No list-typed fields: Pydantic Settings parses those as JSON, so `ME_KINDS=csv,html` would
fail with a decode error that reads like a bug in the tool rather than a malformed value.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ME_", env_file=".env", extra="ignore")

    manifest: Path = Path("data/manifest.jsonl")
    truth: Path = Path("data/truth.txt")
    output_dir: Path = Path("output")

    # VALIDATED by default. The lower tiers exist to be compared against, not to be shipped
    # with -- but `--tier` makes the choice explicit rather than buried.
    min_tier: int = 3

    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings()
