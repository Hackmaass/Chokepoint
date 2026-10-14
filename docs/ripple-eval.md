# Late data ("ripple") evaluation

**Question.** When late or back-dated transfers arrive, `refresh_case` re-runs only the saved findings whose inputs they touched
(found through the `READ` edges from each tool run to the accounts it read), carries the rest forward, and supersedes the old
findings. Does that give the same answers as recomputing everything, and how much work does it save?

**Method** (`scripts/eval_ripple.py`). For 40 held-out cases (10 per scenario): build the four findings (region, money flow, freeze
plan, verification) at data version 0; add one late batch; run `refresh_case`; compare the case's current findings with an
independent twin case computed from scratch at the new data version ("recompute everything", a separate `Toolbox` with its own case
ids). Scenarios, all synthetic and back-dated into the case's window unless noted:

| Scenario | What arrives |
|---|---|
| bypass | a same-length route from the alerted account to a perimeter account through two new accounts (the plan must grow) |
| dead end | one new recipient of the alerted account with no onward transfers |
| unrelated | a transfer between two new accounts the case never read |
| old | a transfer from the alerted account dated a day before the window opens |

**Results**

| | |
|---|---|
| final answers equal to recompute-everything | **40 / 40** |
| changes the scope rule failed to flag | **0** |
| findings re-run / total | 80 / 160 (50%); recompute-everything re-runs 160 |

| Scenario | Findings re-run | Carried forward | Re-run but unchanged | Correct |
|---|---|---|---|---|
| bypass | 40 | 0 | 10 | 10/10 |
| dead end | 40 | 0 | 30 | 10/10 |
| unrelated | 0 | 40 | 0 | 10/10 |
| old (before the window) | 0 | 40 | 0 | 10/10 |

**Reading it honestly.**

- Correctness is the main result: no missed change, and the refreshed state equals a fresh computation in every case.
- The saving is real where the late data does not touch the case (unrelated and out-of-window transfers re-run nothing) and is
  zero where it does: touching any account a case read re-runs all four of its findings, because they share the same read scope.
  In the dead-end scenario only the region summary actually changed, so three of the four re-runs were wasted. The rule is
  deliberately conservative (a false re-run costs time, a missed one costs correctness); finer scopes per finding kind would be the
  next improvement.
- Plans with lambda greater than 0 also depend on older activity (their costs), so for those the rule also looks at transfers
  before the window; a lambda 0 plan does not, which is why the "old" scenario re-runs nothing here.
- Synthetic late batches on synthetic data, 40 cases, one batch per case; not a claim about real late-arrival patterns.
