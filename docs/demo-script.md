# Demo video script (target 2:45, hard cap 3:00)

Record at 1440x900 in dark mode. Use **Replay** mode for the agent steps (fast and repeatable; the interface labels it
as a saved run, and the narration says so) and **Live agent** once, uncut, if time allows. Example alert:
`021174:800737690` at `2022-09-10T10:17` (the plan freezes 3 accounts; the rule of thumb leaves about $19,300 of
$22,400 movable).

| Time | On screen | Say (plain words) |
|---|---|---|
| 0:00 | Empty state: "Where can the money still go?" | "Scam money is moved on within minutes. By the time an alert reaches an investigator, the question is: where can it still go, and which few accounts do we freeze, without locking out everyone else?" |
| 0:15 | Pick the example, click **Investigate** (Replay is the default mode; "Run the example alert" in the middle does the same for the first example). Steps appear one by one. | "Chokepoint is an agent on FalkorDB. It opens a case, computes how much money could still move, builds a freeze plan, and checks it. The model picks the tools; the numbers on the cards come from the tools, and its summary is checked against them." |
| 0:35 | Graph fills in; hero shows **$22,410 can move along these routes**; plan line "Freeze 3 accounts". | "Up to twenty-two thousand dollars can still reach accounts three hops away. Freezing these three accounts cuts every route." |
| 0:52 | Verified chip; routes fade. Point at the method note under the chip. | "It then deletes those accounts from a copy of the graph and looks for any route left. None. This is a consistency check on the same data, and the card says so." |
| 1:08 | **Compare** tab. | "Here is the same number of accounts chosen the way an analyst would without a graph: freeze whoever the account paid most recently. It leaves nineteen thousand dollars still able to move, and its picks are not on the money's routes. On the held-out evaluation the two block about the same share of escaped money, but this plan is more precise and comes with the guarantee. I'm not claiming it blocks more. This example was picked because the rule leaves money movable; the typical result is a tie on blocked share." |
| 1:35 | **Audit** tab, click **Ask the auditor**. | "A second agent sees only what the first stored in the graph. It runs checks and records a verdict; the code won't let it agree while a check fails." |
| 1:52 | Click **Approve a freeze order**, type a name, **Record freeze order**. | "A person, not the agent, approves. The order is recorded in the graph and tied to this checked plan by a hash. It says no bank is called; I would send it on myself. If newer transfers arrive, the plan can no longer be approved, which you'll see in a moment." |
| 2:12 | Click **Simulate late transfers**, then banner, **Re-run affected findings**; **Changes** tab. | "Close the app and come back: the case is still there. Late transfers arrive. Only findings that read the touched accounts are re-run; the old ones are kept and marked superseded. For this alert, movable money goes from about twenty-two thousand to about forty-six thousand dollars and the plan grows from three accounts to four." |
| 2:32 | **Receipt** tab, then the repository page. | "Every answer has a receipt. Everything is open source on synthetic data, with the evaluation protocol and the FalkorDB bug I found written up." |

Before recording: `uv run python scripts/load_ibm.py` state is clean (`POST /api/demo/reset`), API and UI running,
recordings present in `replays/`.
Do not say "hidden chokepoints". Quote only numbers from `docs/pilot-results.md`.
No browser handy? `docker exec chokepoint-api python scripts/demo.py` prints the same chain in the terminal (see docs/deploy.md).
The graph has a **Play routes** button that re-runs the route animation (routes appear hop by hop, then fade when the check lands); use it for a second take.
Silent practice footage of these beats (Replay, no model calls; you narrate over it): `cd web && BASE_URL=http://127.0.0.1:8080 OUT_DIR=<folder outside the repo> node e2e/record-demo.mjs` (it resets the demo data before and after).
