"""Env-overridable configuration for o_chat.

Maps the OS's /etc/o/config concerns onto host development via environment
variables: O_HOME, O_SERVER, O_MODEL.
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SERVER_URL = "http://127.0.0.1:8080"
DEFAULT_MODEL_NAME = "qwen3.5-4b-instruct"


@dataclass(frozen=True)
class Config:
    server_url: str
    model_name: str
    data_home: Path
    history_path: Path


def load_config(env: dict[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    data_home = Path(env.get("O_HOME", Path.home() / ".local" / "share" / "o"))
    data_home.mkdir(parents=True, exist_ok=True)
    return Config(
        server_url=env.get("O_SERVER", DEFAULT_SERVER_URL),
        model_name=env.get("O_MODEL", DEFAULT_MODEL_NAME),
        data_home=data_home,
        history_path=data_home / "history.jsonl",
    )
