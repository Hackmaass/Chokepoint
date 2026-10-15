"""FalkorDB connection helpers."""

from __future__ import annotations

from falkordb import FalkorDB

from .config import get_settings


def connect(socket_timeout: float | None = None, socket_connect_timeout: float | None = None) -> FalkorDB:
    """A client for the configured database. Scripts use the defaults (no socket timeouts: bulk loads run long);
    the API passes timeouts so a dead or restarting database fails a request instead of hanging a worker thread."""
    s = get_settings()
    return FalkorDB(host=s.falkordb_host, port=s.falkordb_port, password=s.falkordb_password,
                    socket_timeout=socket_timeout, socket_connect_timeout=socket_connect_timeout)
