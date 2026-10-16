"""The terminal walkthrough runs all steps and prints only figures that the tools returned."""

from __future__ import annotations

import pytest

from chokepoint.db import connect
from chokepoint.toolbox import Toolbox
from chokepoint.walkthrough import default_alert, run_walkthrough, usd

pytestmark = pytest.mark.db


def test_walkthrough_prints_tool_figures_only():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    account, alert_ts = default_alert()
    lines: list[str] = []
    out = run_walkthrough(Toolbox(db), account, alert_ts, say=lines.append)
    text = "\n".join(lines)

    assert set(out) == {"open_case", "flow", "freeze_plan", "verify_plan", "explain_finding"}
    plan = out["freeze_plan"]
    cp = plan["chokepoint_plan"]
    assert usd(plan["money_movable_before_usd_cents"]) in text
    assert usd(cp["residual_movable_usd_cents"]) in text
    assert all(a in text for a in cp["accounts"])
    assert plan["finding_id"] in text and plan["plan_id"] not in ("", None)
    assert f"verified: {'yes' if out['verify_plan']['verified'] else 'NO'}" in text
    # the honest framing must stay in the output
    assert "typical case is a tie" in text
    for banned in ("fewest accounts", "cannot keep moving", "proves"):
        assert banned not in text


def test_walkthrough_stops_cleanly_on_unknown_account():
    db = connect()
    if "tenant_ibm" not in db.list_graphs():
        pytest.skip("tenant_ibm not loaded")
    lines: list[str] = []
    out = run_walkthrough(Toolbox(db), "000000:NOSUCHACCT", "2022-09-05T10:00", say=lines.append)
    assert "flow" not in out
    assert any("stopped:" in ln for ln in lines)
