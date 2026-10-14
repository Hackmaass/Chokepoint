# What the guarantee costs, and how fast it is (label-free)

`scripts/eval_budget_latency.py`, run on every laundering pattern with five or more transfers (alert at 50% of the pattern),
using only the case's own pre-alert graph: no laundering labels are used to plan or to score. 102 cases had a route to the
perimeter, an actionable plan (25 accounts or fewer) and a verified result.

## Accounts needed to guarantee that no route to the perimeter is left

Chokepoint's minimum cut is the smallest possible number by construction. For comparison, each simple rule freezes accounts in
its own order until every route is cut:

| Method | Median accounts | Mean | Needs more than the minimum | Median ratio to the minimum |
|---|---|---|---|---|
| Chokepoint (minimum cut) | 1 | 2.2 | 0% | 1.00 |
| highest degree | 2 | 4.1 | 47% | 1.00 |
| most recent recipients | 3 | 4.2 | **64%** | 1.67 |
| highest throughput | 2 | 5.5 | 70% | 2.00 |

This is the same point as the held-out evaluation seen from the other side: the rules block about as much escaped money at equal
budget, but when the goal is "leave no route", they need more accounts, and they do not know when they are done; the cut does,
and `verify_plan` checks it.

## Latency, alert to verified plan (tools only, no model)

Open case, money flow, freeze plan and verification: **median 0.27 s, 90th percentile 0.30 s, maximum 0.49 s** over the same 102 cases
(cold: each case was new). With the live agent the end-to-end time is dominated by the model (about 20 to 30 seconds for five calls
on the free API). The "minutes, not hours" claim in the README is about this tool path.

## Limits

Synthetic data; the perimeter model is a modelling choice; cases are selected from labelled patterns (labels are not used by the
planner or in these numbers); regions larger than 4,000 accounts are out of scope for the demo.
