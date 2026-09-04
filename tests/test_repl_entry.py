import pytest

from sleuth.repl_entry import _build_config, _build_parser


def test_build_parser_defaults_path_to_cwd():
    args = _build_parser().parse_args([])
    assert args.path == "."


def test_build_parser_accepts_explicit_path():
    args = _build_parser().parse_args(["some/dir"])
    assert args.path == "some/dir"


def test_build_config_never_uses_a_real_groq_model_name():
    # Agentic mode ignores config.groq_model entirely (AgentSession hardcodes
    # AGENTIC_GROQ_MODEL) — asserting the placeholder stays obviously inert
    # rather than accidentally becoming a real model id someone forgets to
    # wire up.
    config = _build_config("gsk-test-key")
    assert config.groq_api_key == "gsk-test-key"
    assert "unused" in config.groq_model
    assert config.voyage_api_key == "unused"
    assert config.database_url == "unused"


@pytest.mark.asyncio
async def test_main_resolves_key_and_launches_repl(monkeypatch):
    calls = {}

    def fake_resolve():
        return "resolved-key"

    async def fake_run_repl(path, config):
        calls["path"] = path
        calls["config"] = config

    monkeypatch.setattr("sleuth.repl_entry.resolve_groq_api_key", fake_resolve)
    monkeypatch.setattr("sleuth.repl_entry.run_repl", fake_run_repl)

    from sleuth.repl_entry import _main

    await _main(["some/dir"])

    assert calls["path"] == "some/dir"
    assert calls["config"].groq_api_key == "resolved-key"
