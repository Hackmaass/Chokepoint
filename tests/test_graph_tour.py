"""scripts/graph_tour.py shows the graph doing the work, never files a real issue, and cleans up after itself."""

import sys
from pathlib import Path

import pytest

from chokepoint.db import connect

pytestmark = pytest.mark.db
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_the_tour_runs_and_leaves_nothing_behind(capsys, monkeypatch):
    if "tenant_ibm" not in connect().list_graphs():
        pytest.skip("tenant_ibm not loaded")
    monkeypatch.setenv("OPS_GITHUB_TOKEN", "must-be-ignored")  # the tour drops it, so it can never file anything
    import graph_tour

    assert graph_tour.main() == 0
    out = capsys.readouterr().out
    assert "falkordb+repair" in out and "scratch graphs left behind: 0" in out and "Dispatch queued" in out
    db = connect()
    g = db.select_graph("tenant_ibm")
    assert g.ro_query("MATCH (a:Account) WHERE a.hold IS NOT NULL RETURN count(a)", timeout=30000).result_set == [[0]]
