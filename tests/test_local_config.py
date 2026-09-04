import json

import pytest

from sleuth.local_config import (
    load_saved_groq_api_key,
    resolve_groq_api_key,
    save_groq_api_key,
)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "config.json"
    save_groq_api_key("gsk-real-key", path=path)

    assert load_saved_groq_api_key(path=path) == "gsk-real-key"


def test_load_returns_none_when_file_missing(tmp_path):
    assert load_saved_groq_api_key(path=tmp_path / "does_not_exist.json") is None


def test_load_returns_none_on_corrupt_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("not valid json {{{")

    assert load_saved_groq_api_key(path=path) is None


def test_resolve_prefers_env_var_over_saved_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_groq_api_key("saved-key", path=path)
    monkeypatch.setenv("GROQ_API_KEY", "env-key")

    assert resolve_groq_api_key(path=path) == "env-key"


def test_resolve_returns_saved_key_without_prompting(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_groq_api_key("saved-key", path=path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    def fail_prompt(_msg):
        raise AssertionError("should not prompt when a saved key exists")

    assert resolve_groq_api_key(prompt=fail_prompt, path=path) == "saved-key"


def test_resolve_prompts_and_saves_on_first_run(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    prompts = []

    def fake_prompt(msg):
        prompts.append(msg)
        return "entered-key"

    result = resolve_groq_api_key(prompt=fake_prompt, path=path)

    assert result == "entered-key"
    assert len(prompts) == 1
    assert load_saved_groq_api_key(path=path) == "entered-key"


def test_resolve_reprompts_on_empty_input(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    responses = iter(["   ", "", "real-key"])

    def fake_prompt(_msg):
        return next(responses)

    result = resolve_groq_api_key(prompt=fake_prompt, path=path)

    assert result == "real-key"


def test_saved_file_contains_expected_json_shape(tmp_path):
    path = tmp_path / "config.json"
    save_groq_api_key("gsk-abc", path=path)

    assert json.loads(path.read_text()) == {"groq_api_key": "gsk-abc"}
