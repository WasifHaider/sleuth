import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
REQUIRED_VARS = ("VOYAGE_API_KEY", "GROQ_API_KEY", "DATABASE_URL")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    voyage_api_key: str
    groq_api_key: str
    groq_model: str
    database_url: str
    generation_provider: str = "groq"
    nim_api_key: str | None = None


def load_config() -> Config:
    missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    return Config(
        voyage_api_key=os.environ["VOYAGE_API_KEY"],
        groq_api_key=os.environ["GROQ_API_KEY"],
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        database_url=os.environ["DATABASE_URL"],
        generation_provider=os.environ.get("GENERATION_PROVIDER", "groq"),
        nim_api_key=os.environ.get("NIM_API_KEY") or None,
    )
