"""Configuration from the environment. No secrets live in the repo.

Local development reads a git-ignored ``.env`` in the project root and, for the LLM key,
``~/.graphhacks.env``. Real environment variables always win.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_env() -> None:
    _load_env_file(PROJECT_ROOT / ".env")
    _load_env_file(Path.home() / ".graphhacks.env")


@dataclass(frozen=True)
class Settings:
    falkordb_host: str
    falkordb_port: int
    falkordb_password: str | None
    llm_base_url: str
    llm_model: str
    llm_api_key: str | None
    llm_rpm_limit: int

    def __repr__(self) -> str:  # never leak secrets in logs
        return (
            f"Settings(falkordb={self.falkordb_host}:{self.falkordb_port}, "
            f"llm={self.llm_model}@{self.llm_base_url}, secrets=<hidden>)"
        )


def get_settings() -> Settings:
    load_env()
    return Settings(
        falkordb_host=os.environ.get("FALKORDB_HOST", "127.0.0.1"),
        falkordb_port=int(os.environ.get("FALKORDB_PORT", "6379")),
        falkordb_password=os.environ.get("FALKORDB_PASSWORD"),
        llm_base_url=os.environ.get("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        llm_model=os.environ.get("LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b"),
        llm_api_key=os.environ.get("LLM_API_KEY") or os.environ.get("NVIDIA_API_KEY"),
        llm_rpm_limit=int(os.environ.get("LLM_RPM_LIMIT", "30")),
    )
