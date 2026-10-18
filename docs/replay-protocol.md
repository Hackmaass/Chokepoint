# Replay protocol: "watch the money stop" (written 2026-10-19, before `scripts/eval_replay_hold.py` was run)

This is a new, separate protocol. It is **not** the held-out evaluation (`docs/pilot-protocol.md`), which is not re-run or tuned.

**What is replayed.** The recorded synthetic transfers *after* the alert time, in the 48 hours after it, between accounts of the
case region (the accounts within the perimeter hops of the alerted account). The planner reads only transfers up to the alert
time, so replaying later transfers changes no plan, order or finding. The replay reads the graph; it writes nothing.

**Following the money.** The alerted account is "tainted" at the alert time. Going through the post-alert transfers in time order
(ties in the order the database returns them), a transfer is considered only if its sender is tainted:

- **held** if the sender or the receiver is a frozen account of the plan being replayed: the amount is added to *held* and the
  receiver does not become tainted through it;
- **crossed** if the receiver is a perimeter account and the transfer is not held: the amount is added to *crossed the perimeter*;
- **moved** otherwise: the amount is added to *moved inside*, and the receiver becomes tainted.

Amounts are the recorded amounts converted to USD cents with the fixed rates in `src/chokepoint/fx.py`.

**Comparison.** The same replay is run with the same-size rule-of-thumb plan stored in the same freeze_plan finding ("freeze the
accounts the alerted account paid most recently", same number of accounts), so both plans see identical transfers.

**Metric and reporting.** For each of the 8 example alerts (lambda 0, default window and hops) report held and crossed for the
Chokepoint plan and for the rule-of-thumb plan, and the totals over the examples that have at least one tainted post-alert transfer.
Whatever the numbers are, they are reported; if Chokepoint does not hold more or let less cross, the feature stays as a visualisation
and the write-up says so. No significance claim is made: 8 hand-picked examples (chosen where the rule of thumb leaves money movable
before the alert) are not a sample.

**Limits stated up front.** Synthetic data; recorded transfers are not a forecast and did not react to any freeze (in reality
launderers would adapt); the example alerts were not chosen for this metric but were chosen for another one; amounts are recorded
cents, not balances; transfers between accounts outside the region are not followed.

## Result (run once on 2026-10-19 with `uv run python scripts/eval_replay_hold.py`, after this protocol was written)

| alert | accounts | followed transfers | Chokepoint held | Chokepoint crossed | rule held | rule crossed |
|---|---|---|---|---|---|---|
| 021174:800737690 | 3 | 0 | $0 | $0 | $0 | $0 |
| 024515:80EBB2100 | 2 | 0 | $0 | $0 | $0 | $0 |
| 015231:803A568D0 | 3 | 19 | $2,950 | $0 | $2,236,613 | $0 |
| 0119:8000DA7D0 | 3 | 0 | $0 | $0 | $0 | $0 |
| 0135403:80E2226E0 | 3 | 0 | $0 | $0 | $0 | $0 |
| 0111631:80A38F130 | 2 | 0 | $0 | $0 | $0 | $0 |
| 012963:80296CA20 | 2 | 0 | $0 | $0 | $0 | $0 |
| 0113523:8054100E0 | 3 | 10 | $0 | $0 | $0 | $0 |

**Reading it honestly.** In 6 of the 8 examples no recorded transfer in the 48 hours after the alert leaves the alerted account's
network, so there is nothing to replay. In the other two, nothing crosses the perimeter under either plan: the result is a tie at
$0 crossed, and it shows no advantage for the Chokepoint plan. The held amounts differ, but "held" counts every followed transfer
that touches a frozen account, so a larger number only says that the rule-of-thumb accounts sat on a bigger recorded flow; it is
not a better outcome. No claim is made from this. The replay stays in the app as a visualisation of what the recorded transfers did
after the alert, labelled as such.
