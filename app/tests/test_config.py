"""Config loading for o_chat: env-overridable paths and server settings."""

from pathlib import Path

from o_chat.config import load_config


def test_defaults(tmp_path, monkeypatch):
    for var in ("O_HOME", "O_SERVER", "O_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config()
    assert cfg.server_url == "http://127.0.0.1:8080"
    assert cfg.model_name == "qwen3.5-4b-instruct"
    assert cfg.data_home == tmp_path / ".local" / "share" / "o"
    assert cfg.history_path == cfg.data_home / "history.jsonl"
    assert cfg.data_home.is_dir()


def test_defaults_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("O_HOME", str(tmp_path / "o"))
    monkeypatch.setenv("O_SERVER", "http://localhost:9999")
    monkeypatch.setenv("O_MODEL", "agents-a1-4b")
    cfg = load_config()
    assert cfg.server_url == "http://localhost:9999"
    assert cfg.model_name == "agents-a1-4b"
    assert cfg.history_path == tmp_path / "o" / "history.jsonl"
    assert cfg.data_home.is_dir()
    assert isinstance(cfg.data_home, Path)
