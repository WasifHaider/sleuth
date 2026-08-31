import os
import warnings
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_FRONTEND_ORIGIN = "http://localhost:5717"
REQUIRED_VARS = ("VOYAGE_API_KEY", "GROQ_API_KEY", "DATABASE_URL")
INSECURE_DEFAULT_SESSION_SECRET = "dev-insecure-session-secret"


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
    session_secret: str = INSECURE_DEFAULT_SESSION_SECRET
    # sleuth/api/main.py's CORSMiddleware used to hardcode this to the local
    # Vite dev server's origin — harmless for local dev (the only thing
    # anyone had actually run against), but any real deployment with the
    # frontend served from a different origin would have every cross-origin
    # request silently blocked by the browser (CORS failures show up as a
    # network error client-side with a message that never mentions the
    # actual cause). Configurable now, defaulting to the same dev origin so
    # local dev needs no .env change.
    frontend_origin: str = DEFAULT_FRONTEND_ORIGIN


def load_config() -> Config:
    missing = [name for name in REQUIRED_VARS if not os.environ.get(name)]
    if missing:
        raise ConfigError(f"Missing required environment variable(s): {', '.join(missing)}")

    session_secret = os.environ.get("SESSION_SECRET", INSECURE_DEFAULT_SESSION_SECRET)
    if session_secret == INSECURE_DEFAULT_SESSION_SECRET:
        # The session cookie (sleuth/api/auth/session.py) signs and embeds a
        # user's identity claims — email/name/theme_preference — verified
        # purely by this secret's signature, no DB check on the hot path.
        # This literal string is sitting in the source right above, so
        # anyone who's read this file (or this repo's public history) can
        # forge a valid, signed session cookie for ANY user_id with it.
        # SESSION_SECRET is genuinely optional for a disposable local-dev
        # DB where forging a session gets an attacker nothing real, so this
        # warns loudly rather than refusing to start outright — but it
        # cannot tell "local dev" from "someone forgot to set this in
        # prod" from inside a plain env-var read, so the warning is the
        # honest thing this function can actually do.
        warnings.warn(
            "SESSION_SECRET is not set — falling back to a well-known, "
            "publicly-visible-in-source placeholder. Anyone who has read this "
            "code can forge a valid session cookie for any user. Set a real "
            "SESSION_SECRET (see .env.example) before running against a real "
            "database.",
            RuntimeWarning,
            stacklevel=2,
        )

    return Config(
        voyage_api_key=os.environ["VOYAGE_API_KEY"],
        groq_api_key=os.environ["GROQ_API_KEY"],
        groq_model=os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
        database_url=os.environ["DATABASE_URL"],
        generation_provider=os.environ.get("GENERATION_PROVIDER", "groq"),
        nim_api_key=os.environ.get("NIM_API_KEY") or None,
        session_secret=session_secret,
        frontend_origin=os.environ.get("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGIN),
    )
