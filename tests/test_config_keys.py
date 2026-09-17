"""Where an API key may live, and what happens when it is in the wrong place."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from slcvoiceai.config import _find_stray_key, _resolve_key  # noqa: E402

FAKE_GEMINI = "AIzaSyExampleKeyForTestsOnly1234567890"

STRANDED_CONFIG = "\n".join([
    "[intent]",
    'backend = "fuzzy"',
    "#     setx GEMINI_API_KEY " + FAKE_GEMINI,
    'escalate_to = "gemini"',
    "",
])

ORDINARY_CONFIG = "\n".join([
    "[gemini]",
    'api_key = ""',
    "# nothing key-shaped here",
    "",
])


@pytest.fixture
def chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_inline_key_wins(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "from-environment")
    assert _resolve_key(FAKE_GEMINI, "SOME_VAR", "Gemini", "url") == FAKE_GEMINI


def test_environment_is_the_fallback(monkeypatch):
    monkeypatch.setenv("SOME_VAR", "from-environment")
    assert _resolve_key("", "SOME_VAR", "Gemini", "url") == "from-environment"


def test_missing_key_explains_both_routes(monkeypatch, chdir):
    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        _resolve_key("", "SOME_VAR", "Gemini", "https://example.test")
    message = str(excinfo.value)
    assert "api_key in config.toml" in message
    assert "SOME_VAR" in message
    assert "NEW terminal" in message


def test_key_stranded_in_a_comment_is_pointed_out(monkeypatch, chdir):
    """The first person to configure this pasted their key into the
    commented-out setx example, where TOML ignored it silently and the only
    symptom was a window that flashed and vanished."""
    (chdir / "config.toml").write_text(STRANDED_CONFIG, encoding="utf-8")

    assert _find_stray_key() == (3, "intent")

    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        _resolve_key("", "SOME_VAR", "Gemini", "https://example.test")
    message = str(excinfo.value)
    assert "line 3" in message
    assert "inside a comment" in message


def test_no_false_alarm_on_an_ordinary_config(chdir):
    (chdir / "config.toml").write_text(ORDINARY_CONFIG, encoding="utf-8")
    assert _find_stray_key() is None


def test_missing_config_is_not_an_error(chdir):
    assert _find_stray_key() is None
