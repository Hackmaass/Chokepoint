"""FalkorDB connection helpers."""

from __future__ import annotations

from falkordb import FalkorDB

from .config import get_settings


def connect() -> FalkorDB:
    s = get_settings()
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password)
