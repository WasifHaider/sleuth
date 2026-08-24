import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
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
    github_client_id: str | None = None
    github_client_secret: str | None = None
    session_secret: str = "dev-insecure-session-secret"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str | None = None
    frontend_url: str = "http://localhost:5173"


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
        github_client_id=os.environ.get("GITHUB_CLIENT_ID") or None,
        github_client_secret=os.environ.get("GITHUB_CLIENT_SECRET") or None,
        session_secret=os.environ.get("SESSION_SECRET", "dev-insecure-session-secret"),
        smtp_host=os.environ.get("SMTP_HOST") or None,
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_username=os.environ.get("SMTP_USERNAME") or None,
        smtp_password=os.environ.get("SMTP_PASSWORD") or None,
        smtp_from_address=os.environ.get("SMTP_FROM_ADDRESS") or None,
        frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:5173"),
    )
