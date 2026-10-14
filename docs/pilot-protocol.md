# Pilot protocol (written before the dev-set results were looked at)

Written 2026-10-15 after the first all-data sweeps showed that the best row of a table over 155
components is an optimistic estimate. From here on:

1. **Split.** Components with 5+ laundering transfers are shuffled with a fixed seed (20261015) and
   split 50/50 into `dev` and `test`. Configuration is chosen on `dev` only. `test` is run once
   after the configuration is locked, and only `test` numbers are reported as results.
2. **Scenario.** At alert time `t` the alerted account (origin) is frozen first. Its post-`t`
   outgoing transfers count as blocked for every method. The score of interest is what each method
   contains of the money that **already escaped**: post-`t` laundering transfers that do not touch
   the origin. We also report the share of the whole pattern stopped (including pre-alert transfers).
3. **Baselines at equal budget** (`k` = number of accounts in the plan): top-degree, top-throughput,
   and two analyst baselines: freeze the accounts the origin most recently paid (`recent`), and the
   accounts the origin paid most often (`cnt`). Ties are filled by degree.
4. **Extraction variants.** `static` (all transfers in `[t - W, t]`, forward K hops) and `trail`
   (time-respecting: an edge is used only if its timestamp is at or after the money reached the
   tail account; money starts at the origin when its first laundering transfer is sent, a
   case-definition detail taken from the label timeline, used for selection only).
5. **Lock rule.** Over configurations {static W=3, static W=7, trail} x K in {3, 4}, on `dev`
   with frac = 0.5 and lambda = 0: choose the configuration with the largest
   (paired wins - paired losses) of Chokepoint against the strongest baseline (highest dev mean
   blocked-of-escaped), requiring at least 50 dev cases with a route; ties go to the larger case
   count. The lambda grid is {0, 0.5, 1, 2, 4, 8}; lambda is reported as a trade-off curve, not
   tuned. Alert fractions 0.3 and 0.7 are sensitivity checks on `test` only.
6. **Fall-back trigger.** If Chokepoint has more paired losses than wins against the strongest
   baseline on `dev` in every configuration, the story changes to "verified, receipted,
   collateral-aware freezing" rather than "finds hidden chokepoints"; the idea is abandoned only if
   it also fails to beat top-degree.
7. **Late alerts.** Legitimate traffic stops after Sep 10, so collateral is understated for late
   alerts. Cross-fraction comparisons use cases with `t` on or before Sep 7; the count of later
   cases is reported.
