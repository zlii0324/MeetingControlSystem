from __future__ import annotations

import os
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import config
from mailer import create_smtp_ssl_context


def test_default_loader_reads_env_and_ignores_example(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_example_file = tmp_path / ".env.example"
    env_file.write_text("ENV_LOADING_TEST=from-env\n", encoding="utf-8")
    env_example_file.write_text("ENV_LOADING_TEST=from-example\n", encoding="utf-8")

    monkeypatch.delenv("ENV_LOADING_TEST", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", env_file)

    config.load_dotenv()

    assert os.environ["ENV_LOADING_TEST"] == "from-env"


def test_blank_samesite_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_COOKIE_SAMESITE", "")

    assert config._as_cookie_samesite("TEST_COOKIE_SAMESITE", "Lax") == "Lax"


def test_blank_string_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_STRING_SETTING", "")

    assert config._as_str("TEST_STRING_SETTING", "fallback") == "fallback"


def test_insecure_smtp_context_requires_explicit_choice():
    verified_context = create_smtp_ssl_context(True)
    unverified_context = create_smtp_ssl_context(False)

    assert verified_context.check_hostname is True
    assert unverified_context.check_hostname is False
