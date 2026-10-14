# Pilot results: held-out test split (2026-10-15)

Follows `docs/pilot-protocol.md`. Cases are laundering components with 5+ transfers (155), split 50/50
by a seeded shuffle into dev (77) and test (78). Configuration was chosen on dev; the test half was run
once with the locked configuration. This is an offline NetworkX pilot, not the final evaluation.

## Locked configuration and one deviation

Static extraction over the 7 days before the alert (`static7`), forward 3 hops, perimeter at hop K=3
(K=4 reported as a sensitivity). Lock rule ranking on dev (alert at 50%, lambda=0): static7/K=3 and
static3/K=3 tied at +4 paired wins over losses against the strongest baseline; the larger case count
(static7) won. **Deviation:** the rule required 50 dev cases with a route, which a half-sized dev
set cannot reach (largest: 46), so the minimum was waived. The time-respecting `trail` variant was not
better than static extraction on dev. Cost: `100 + round(lambda * 100 * log1p(base) / log1p(max_base))`.
On dev, plans for lambda in {0.5 ... 8} were identical to each other (lambda acts mainly as a
tie-breaker here), so the trade-off curve in this data is nearly a two-point curve.

## Scenario

At alert time `t` the alerted account is frozen first (its post-`t` outgoing transfers count as blocked
for every method). The score is **blocked**: the share of post-`t` laundering transfers of the pattern
that do not involve the alerted account (money that already escaped) and touch a frozen account.
Baselines freeze the same number of accounts as the plan: top-degree, top-throughput, `rec` (the
accounts the alerted account most recently paid), `cnt` (the accounts it paid most often).

## Test results (K=3, lambda=0; 40-43 cases with a route per alert time)

| Alert at | blocked CP | deg | thr | rec | cnt | precision CP / rec | median collateral CP / deg / rec |
|---|---|---|---|---|---|---|---|
| 30% | 0.169 | 0.096 | 0.067 | 0.224 | 0.052 | 0.68 / 0.61 | 7 / 8 / 6 |
| 50% | 0.209 | 0.148 | 0.062 | 0.207 | 0.097 | 0.73 / 0.52 | 7 / 7 / 6 |
| 70% | 0.338 | 0.325 | 0.090 | 0.292 | 0.155 | 0.78 / 0.50 | 7 / 7 / 6 |

Pooled over the three alert times (K=3, lambda=0, 124 case-times; cases repeat, so the interval is
optimistic): mean difference in blocked share, Chokepoint minus baseline, with bootstrap 95% interval:
vs top-degree +0.049 [+0.012, +0.088]; vs top-throughput +0.166 [+0.110, +0.224]; vs `cnt` +0.137
[+0.085, +0.195]; vs `rec` **-0.002 [-0.064, +0.061]**.

At K=4 the plan blocks about as much as `rec` at 30% and 50% and more at 70% (0.326 vs 0.214; paired
6 wins, 29 ties, 2 losses against degree, mean difference +0.095 [-0.018, +0.223]), with lower median
collateral than degree and throughput (8 vs 14-18 at 50%).

## What this does and does not show

- The advantage seen on dev against `rec` (+8 net paired wins at a 70% alert) **did not replicate** on
  test. Against the analyst baseline "freeze the accounts the alerted account most recently paid", the
  plan blocks the same share within noise at every alert time. It beats top-degree, top-throughput and
  most-frequent-recipients.
- The plan is more precise: 0.68-0.82 of the frozen accounts are laundering accounts, against about
  0.5-0.6 for `rec`. Collateral (legitimate transfers touched in the next 3 days) is about the same as `rec`.
- Median plans freeze about 2 accounts. Most cuts sit at hop 1, so the strongest simple heuristic usually
  picks the same accounts ("ties" are the majority of paired comparisons).
- Limits: synthetic data; labels used to select cases and to score; counts not amounts (mixed
  currencies); a plan from past transfers cannot see transfers between accounts that have not dealt with
  each other yet (about 55-60% of cases block nothing for every method); 40-43 usable cases per alert time.
- The claim "finds hidden chokepoints that simple heuristics miss" is **not supported** by this
  evidence. Supported claims: equal blocking to the best heuristic with higher precision, a guarantee that
  every route to the perimeter is cut (checked independently with `shortestPath` after deleting the frozen
  accounts), and a stored receipt for every result.
