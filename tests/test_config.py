import pytest
from sleuth.config import load_config, ConfigError


def test_load_config_reads_all_values(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.voyage_api_key == "voyage-key"
    assert config.groq_api_key == "groq-key"
    assert config.groq_model == "llama-3.3-70b-versatile"
    assert config.database_url == "postgresql://u:p@localhost:5432/db"


def test_load_config_defaults_groq_model(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-key")
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    config = load_config()

    assert config.groq_model == "llama-3.3-70b-versatile"


def test_load_config_raises_on_missing_required_var(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

    with pytest.raises(ConfigError, match="VOYAGE_API_KEY"):
        load_config()
