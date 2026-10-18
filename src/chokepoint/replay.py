"""Replay of the recorded transfers after the alert (docs/replay-protocol.md). Pure: it reads recorded transfers and writes nothing.

Starting from the alerted account, transfers are followed in time order. A transfer whose sender is "tainted" is *held* if the
sender or receiver is a frozen account, *crossed* if its receiver is a perimeter account, and otherwise *moved* (the receiver
becomes tainted). Recorded data, not a forecast: the real transfers did not react to any freeze.
"""

from __future__ import annotations

REPLAY_HOURS = 48


def simulate(transfers: list[dict], origin: str, perimeter: set[str], frozen: set[str]) -> tuple[list[dict], dict[str, int]]:
    """``transfers`` are dicts {s, d, ts, usd, tid} in time order. Returns the followed transfers with a status, and totals."""
    tainted = {origin}
    events: list[dict] = []
    totals = {"held": 0, "crossed": 0, "moved": 0}
    for t in transfers:
        if t["s"] not in tainted:
            continue
        if t["s"] in frozen or t["d"] in frozen:
            status = "held"
        elif t["d"] in perimeter:
            status = "crossed"
        else:
            status = "moved"
            tainted.add(t["d"])
        totals[status] += t["usd"]
        events.append({**t, "status": status})
    return events, totals
