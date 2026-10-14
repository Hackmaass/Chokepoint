# Data gate (G7) and the hour-0 pilot (2026-10-15)

Offline pilot (NetworkX, `scripts/g7_pilot.py`, `scripts/g7_report.py`). Numbers are from a pilot,
not the final evaluation. Labels are used only to select cases and to score.

## Dataset

IBM synthetic AML transactions, "HI-Small" (Altman et al., NeurIPS 2023 Datasets and Benchmarks,
arXiv:2306.16424). Licence CDLA-Sharing-1.0, confirmed from the dataset owner's Kaggle metadata.
We use the Hugging Face mirror `eexzzm/IBM-Transactions-for-Anti-Money-Laundering-HI-Small-Trans`
(no licence card on the mirror). The data is not committed.

| Fact | Value |
|---|---|
| zip | 91,084,571 bytes, sha256 `4407435ec99e37262294b3d4e317f3adcb8a89caa0df66927d1c6ba4f46e8080` |
| rows / accounts / bank ids | 5,078,345 / 515,080 / 30,528 (median 4 accounts per bank) |
| time range | 2022-09-01 00:00 to 2022-09-18 16:18 |
| laundering-labelled transfers | 5,177 (0.10%) in 1,843 connected components |
| components with 1 / 5+ / 10+ / 20+ transfers | most are a single transfer / 155 / 111 / 40 |
| payment formats (laundering rows) | ACH 4,483 · Cheque 324 · Credit Card 206 · Cash 108 · Bitcoin 56 · Wire 0 · Reinvestment 0 |
| legitimate traffic | essentially stops after Sep 10; later rows are almost all laundering |
| patterns file | not on the mirror, so pattern groups are connected components of laundering transfers |

Larger components are scatter-gather shaped: one origin pays several intermediaries, which all pay
one collector. Laundering accounts also carry normal traffic (6,288 of 6,357).

## Exit rule

Cash-like formats are not where laundering ends in this data (164 of 5,177 laundering transfers), and
"any account at another bank" would make most transfers exits (86% of transfers are cross-bank).
The pilot therefore uses a **hop perimeter**: accounts reached at exactly K hops from the alerted
account in the pre-alert graph are the exits ("beyond K hops the money is out of reach"). The alerted
account is not freezable (its money has already moved on). Consequence: a finite cut always exists
when a route to the perimeter exists, so the failure the agent reports is `NO_PATH`
(no route to the perimeter), not `NO_FINITE_CUT` (still handled in code).

## Pilot design

- Case: origin of a laundering component with 5+ transfers; alert time `t` at a fraction of that
  component's transfers (0.3, 0.5, 0.7). Selection uses labels (documented limitation).
- Plan graph: transfers in `[t - W, t]`, forward from the origin up to K hops; costs are label-free
  (`100 + round(λ·100·log1p(base)/log1p(max_base))`, base = pre-alert activity outside the region).
- Score: share of the component's post-`t` laundering transfers that have a frozen account as
  sender or receiver ("blocked"); count of post-`t` legitimate transfers in the next 3 days that touch
  a frozen account ("collateral"). Counts, not amounts (the data mixes currencies).
- Baselines at equal budget (same number of accounts as the plan): top-degree, top-throughput.

## Results (155 components, 20 skipped: 16 with no pre-alert outgoing transfer, 4 regions over 4,000 accounts)

Cases with a route to the perimeter, mean of per-case values:

| Alert at | History W | K | λ | cases | blocked CP / degree / throughput | collateral median CP / degree | CP better / equal / worse than degree |
|---|---|---|---|---|---|---|---|
| 30% | 3 d | 3 | 0 | 85 | 15.5% / 10.9% / 7.9% | 14 / 17 | 21 / 61 / 3 |
| 50% | 3 d | 3 | 0 | 91 | 18.2% / 13.0% / 9.3% | 12 / 14 | 25 / 63 / 3 |
| 50% | 3 d | 3 | 1 | 91 | 16.0% / 13.0% / 9.3% | 11 / 14 | 18 / 65 / 8 |
| 50% | 3 d | 4 | 1 | 77 | 11.6% / 7.9% / 5.2% | 10 / 15 | 16 / 56 / 5 |
| 70% | 3 d | 3 | 0 | 87 | 23.1% / 22.7% / 15.2% | 12 / 14 | 11 / 68 / 8 |
| 50% | 7 d | 3 | 0 | 106 | 24.1% / 16.7% / 8.3% | 12 / 10 | 26 / 79 / 1 |
| 70% | 7 d | 3 | 0 | 116 | 37.9% / 32.4% / 14.5% | 8 / 7 | 12 / 98 / 6 |
| 70% | 7 d | 3 | 1 | 116 | 31.7% / 32.4% / 14.5% | 6 / 7 | 9 / 97 / 10 |

## Reading it honestly

- The plan beats equal-budget top-degree and top-throughput on average, mostly where the two differ;
  in most cases they pick the same accounts (the "equal" column).
- Absolute effect is limited by prediction: a plan built from the past cannot see transfers between
  accounts that have not dealt with each other yet. About 55-60% of cases block nothing at all (for
  every method); about 73% of post-alert laundering transfers touch the plan region (the ceiling).
- More history helps (7 d better than 3 d). A later alert blocks a larger share of the *remaining* transfers
  because more of the pattern is visible, but there is less left to stop: the share of the *whole* pattern
  stopped falls with delay (see `docs/pilot-results.md`). Late alerts also understate collateral because
  legitimate traffic stops after Sep 10.
- This first pilot reused all 155 components for every sweep, so its best rows are optimistic. The held-out
  results in `docs/pilot-results.md` supersede it.
- λ > 0 trades some blocked laundering for lower collateral, as designed; at a 70% alert it falls
  slightly below degree on blocked share.
- Collateral means are dominated by a few very large plans; medians are reported above.
- Not yet measured: top-betweenness baseline, LLM-on-text ablation, amounts, bootstrap intervals.
