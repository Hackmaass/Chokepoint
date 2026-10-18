# Which mule accounts do you freeze? A verified cut on FalkorDB, and a bug I found on the way

*Draft for the Graph Hacks blog prize. Numbers marked TODO are filled in from the final runs.*

Scam money does not sit still. In the first minutes after it lands in a mule account it is spread across other accounts, and by the time an alert reaches an investigator the question is no longer "is this account bad" but "where can the money still go, and which few accounts do we freeze to stop it, without locking out everyone else". That is a question about how accounts connect, which is why I built **Chokepoint** on FalkorDB for Graph Hacks.

This post is about what worked, what did not, and one thing I found in FalkorDB along the way. I'll start with the part I expected to be the headline and wasn't.

## The result that did not go my way

I evaluated the freeze plans on the IBM synthetic anti-money-laundering data (5 million transfers, 155 laundering patterns with five or more transfers). Cases were split in half by pattern; I chose every setting on one half and ran the other half **once**.

At equal budget, my minimum-cost vertex cut blocked about the same share of the laundering money that had already escaped as a one-line rule an analyst would use without any graph: *freeze the accounts the alerted account paid most recently* (difference -0.002, 95% interval -0.09 to +0.08, bootstrapped by pattern). On the half I had tuned on, it looked better than that rule. It did not replicate. I wrote that down in the README instead of the version I had hoped for.

What the graph did buy:

- **Precision.** The accounts it freezes are more often part of the laundering (+0.19, interval +0.07 to +0.32). 61% of the innocent accounts the rule of thumb picks are accounts the money cannot even pass through.
- **A guarantee, and a smaller one.** Every account on the plan lies on a route, and after deleting them from a copy of the graph, `shortestPath` finds no route left to the perimeter. To get the same guarantee the one-line rule needs more accounts than the minimum cut in 64% of cases (median 3 against 1). With a same-size rule-of-thumb plan, routes stay open in the worked examples (about $19k of $22k still movable in one).
- **Receipts and memory.** Every result is a node linked to the tool run, parameters, data version and the accounts it read. That is what makes the next section possible.

## Late data: re-run only what changed

When a late or back-dated transfer arrives, which of the saved findings are now wrong? Re-running everything is correct but wasteful. In Chokepoint each finding is linked by `READ` edges to the accounts its tool run looked at, so the question becomes one traversal: *which current findings read an account that has a new transfer in the window?* Only those are re-run; the old ones are marked superseded and linked to their replacements; the rest are carried forward.

I tested this on 40 held-out cases with four kinds of late batch (a bypass route, a dead-end recipient, an unrelated transfer, an old out-of-window transfer), against an independent twin case recomputed from scratch at the new data version. The refreshed answers were equal to a full recomputation in **40 of 40** cases, the scope rule **missed no change**, and it re-ran half of the findings. The honest part: savings come from late data that does not touch the case; when it does, every finding of that case is re-run (they share a scope) and half of those re-runs were unchanged. A finer scope per finding is the next step.

## A FalkorDB finding

The core of the plan is a minimum-cost vertex cut. Max-flow gives an *edge* cut, so each account is split into an in-node and an out-node joined by an edge whose capacity is the cost of freezing it; FalkorDB's `algo.maxFlow` then returns a flow value and edge flows, and the cut is read off the residual graph.

While testing that against NetworkX on random networks I found that `algo.maxFlow` sometimes returns a flow that is not maximal and does not conserve flow, on both v4.22.0 and 6.0.2: about 17% of the node-split networks I generated. I shrank a failing case to 11 edges (true maximum 453, FalkorDB returns 300). Plain random graphs without the node splitting were all correct, so it is a structural trigger, not capacities or cycles.

So I did not trust it blindly. A FalkorDB result is accepted only with a certificate: the flow must be feasible, and no sink may be reachable in the residual graph, which by max-flow/min-cut duality proves it optimal. Otherwise the same network is solved exactly in NetworkX. Over 120 random networks: zero false accepts, and the final plans equal the NetworkX optimum on all of them. About 73% of finite cuts were certified straight from FalkorDB. The repro, the numbers and a draft bug report are in the repository, and a strict `xfail` test will tell me when it gets fixed.

## Money cannot go back in time

My plan, like most graph demos, treated the recorded transfers as a static map. That is conservative (it cuts every route, in or out of order) but it can over-warn. So I added a timing check: starting at the alerted account at the start of the window, keep only transfers that happen no earlier than the money could have arrived, then recompute the cut on what is left. Every time-ordered route survives this, so the result still cuts every real route.

On the eight example alerts: in **three** there is no time-ordered route to the perimeter at all (the static map had routes, but each used a transfer that happened before the money got there), and in **two** the plan shrinks (3 to 1 accounts, 2 to 1). In the other three it is unchanged, including the alert I use in the demo. The check is shown on the card next to the static plan, not instead of it, and it is checked in code on the recorded transfers rather than on the graph copy. I added it after the held-out evaluation, so I make no claim about it beyond these eight examples.

## Things I'd tell myself at hour zero

- **Write the protocol before you look at the results.** I wrote down the dev/test split and the rule for choosing a configuration first. The result that disappeared on the held-out half would have shipped as a headline otherwise.
- **Make tool output deterministic.** I wanted saved agent runs the demo can replay when the free model is slow. They kept missing because tool results were the same facts in a different key order, or carried a flag that depended on what had run before. Ids derived from inputs, canonical JSON, and a test that replays every example fixed it.
- **Servers have operational edges.** `GRAPH.COPY` needs a fork and fails while a background save runs; I turned snapshots off (the append-only file already persists the data), retry, and fall back to building the second graph.
- **The model should choose and explain; code should compute.** The LLM never writes Cypher, the numbers on the cards come only from tools, and its short summary is checked against the tool results (in 120 evaluated runs one narrative mis-transcribed a figure; the interface now flags that). A second agent, the auditor, reads only what the first one stored in the graph and records a verdict that code refuses to let it contradict.

## Try it

Code, data model, every query and the evaluation protocol: https://github.com/Hackmaass/Chokepoint . Live demo: TODO. Everything here uses synthetic data; a freeze plan is a recommendation, not an instruction.
