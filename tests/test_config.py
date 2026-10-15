"""The .env loader: quoting, comments, and the rule that real environment variables win."""

from __future__ import annotations

import os

from chokepoint.config import Settings, _load_env_file


def test_env_file_parsing(tmp_path, monkeypatch):
    for k in ("CP_A", "CP_B", "CP_C", "CP_D", "CP_E"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CP_E", "from-the-environment")
    f = tmp_path / ".env"
    f.write_text('# comment\nCP_A=plain\nCP_B="quoted # not a comment"\nCP_C=60   # trailing comment\n'
                 "CP_D='single'\nCP_E=from-the-file\nnot a setting\n", encoding="utf-8")
    _load_env_file(f)
    assert os.environ["CP_A"] == "plain"
    assert os.environ["CP_B"] == "quoted # not a comment"
    assert os.environ["CP_C"] == "60" and int(os.environ["CP_C"]) == 60
    assert os.environ["CP_D"] == "single"
    assert os.environ["CP_E"] == "from-the-environment"  # real environment variables always win
    for k in ("CP_A", "CP_B", "CP_C", "CP_D"):
        monkeypatch.delenv(k, raising=False)


def test_settings_never_print_secrets():
    s = Settings("h", 1, "db-secret", "https://x", "m", "key-secret", 30)
    assert "db-secret" not in repr(s) and "key-secret" not in repr(s)
