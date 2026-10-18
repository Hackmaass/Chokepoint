---
title: "Which mule accounts do you freeze? A checked minimum cut on a 4.49-million-transfer FalkorDB graph, and a max-flow bug I hit"
published: false
description: "A vertex cut on IBM's synthetic AML transfers, checked on a graph copy, plus an algo.maxFlow wrong-answer repro and a held-out result that did not go my way."
tags: falkordb, graph, python, ai
---

After a scam alert lands on a mule account, "is this account bad" is already answered. The useful question comes next: the money is moving, so which few accounts do you freeze so it loses its routes out, without locking half the bank?

That is a question about how accounts connect, so I built Chokepoint for Graph Hacks on FalkorDB. It runs on IBM's synthetic anti-money-laundering transfers: 5.08 million rows, 4.49 million loaded once self-transfers are dropped. Each case pulls a small region out of that graph (tens to a few thousand accounts), and the cut runs on the region. This post covers how the cut works, a FalkorDB defect I found while building it, and a held-out result I would have preferred to write differently.

Code: https://github.com/Hackmaass/Chokepoint · Live demo: https://chokepoint-uou2.onrender.com

The short version: the plan blocks about the same share of escaped laundering money as a simple rule of thumb, but it is more precise and it comes with a check. Every plan is checked twice, a person approves it, and an approval cannot outlive its evidence. Along the way I found a case where FalkorDB's `algo.maxFlow` returns the wrong answer, and one command now re-measures every number in this post.

![The decision card](https://raw.githubusercontent.com/Hackmaass/Chokepoint/main/web/public/shots/s3-plan.webp)

## Accounts have costs, transfers do not

Max-flow gives you an edge cut. I wanted an account cut, because you freeze accounts, not transfers. The standard trick is node splitting: each account becomes an in-node and an out-node joined by one edge whose capacity is the cost of freezing that account. Every transfer becomes an edge from the sender's out-node to the receiver's in-node with infinite capacity.

Take two routes from the alerted account that both pass through account `h` on the way to the perimeter. If all accounts cost the same, freezing `h` costs one and freezing the two accounts before it costs two, so the minimum edge cut is the `h:in -> h:out` edge. Infinite transfer edges can never be in a minimum cut, which is why the cut can only land on accounts.

The alerted account is the source and the accounts three hops out are the sinks. I build the region with one fixed query per hop, over the transfers in the window:

```cypher
MATCH (a:Account)-[t:TRANSFER]->(b:Account)
WHERE a.id IN $frontier AND t.ts >= $t_from AND t.ts <= $t_to AND t.batch <= $data_version
RETURN a.id AS s, b.id AS d, t.cur AS cur, sum(t.amt) AS amt, count(t) AS n, max(t.ts) AS last
```

Then the split network goes to FalkorDB's `algo.maxFlow` as a first pass:

```cypher
MATCH (s:N {id: $source})
MATCH (t:N) WHERE t.id IN $sinks
WITH s, collect(t) AS ts
CALL algo.maxFlow({sourceNodes: [s], targetNodes: ts, relationshipTypes: ['E'],
                   capacityProperty: 'cap', nodeLabels: ['N']})
YIELD edges, edgeFlows, maxFlow
RETURN maxFlow, [e IN edges | [startNode(e).id, endNode(e).id]] AS ends, edgeFlows
```

It returns a flow value and edge flows, not a cut. I read the cut off the residual graph on the client: the accounts whose in-node is reachable from the source and whose out-node is not.

## The answer that was too small

My assistant wrote a NetworkX oracle to check this against, and in the first hours it disagreed with FalkorDB on a surprising share of random networks. Not on plain random graphs: I ran 260 trials of those, cyclic and acyclic, with assorted capacities, and got zero mismatches. Only on node-split networks.

On those, `algo.maxFlow` sometimes returned a value below the true maximum, and the flows it returned broke conservation, so they were not even a valid flow. Across two batches that was 12 to 17% of the networks I generated (24 of about 140 earlier, then 14 of 120 on a later batch). Both v4.22.0 and 6.0.2 did it. Changing the "infinite" capacity did not change the wrong answer.

I shrank a failing case until it had 11 edges. The source reaches the sink by paths of different lengths, the longest being 7 edges:

```
src:out -> a15:in 3322     a15:in  -> a15:out 158
src:out -> a20:in 3322     a15:out -> a20:in  3322
a20:in  -> a20:out 300     a15:out -> a3:in   3322
a20:out -> x1:in   3322    a3:in   -> a3:out  153
a3:out  -> a12:in  3322    a12:in  -> a12:out 261
a12:out -> x1:in   3322
```

NetworkX says the maximum is 453: 300 through `a20` and 153 through `a3` then `a12`. FalkorDB returns 300, on both versions, and it did again when I re-ran it the day I wrote this. Nine-edge versions of the same shape come out right, so the trigger seems to need the long path alongside the short ones. I did not read the algorithm, and I would not claim to know why.

The second annoyance was a hang: one network sent `algo.maxFlow` into a run that ignored the per-query timeout until the server default (3 seconds, which I set short) cancelled it cleanly.

A defect report is not an attack. `algo.maxFlow` was right on every one of the eight real example networks, and every plan I show used its answer. But I could not ship something that is wrong 12 to 17% of the time on the shape I need, so FalkorDB's result is used only with a certificate: the flow has to be feasible (capacities respected, flow conserved), and no sink may be reachable in the residual graph, which by max-flow/min-cut duality means the flow is maximal. Anything else, whether wrong, errored, empty or timed out, goes to an exact NetworkX solve, and the plan records which solver produced it.

On 40 random networks the script reports 22 accepted, 6 rejected and 12 where FalkorDB gave no usable answer. The raw value differed from exact on 5 of the 40. Zero false accepts there, and zero on a separate batch of 120. The 11-edge repro and a draft bug report are in the repository, with a strict `xfail` test that will tell me when it is fixed. It is still a draft; I have not filed it.

## Checking the plan on a copy

Every plan gets a second look. I rebuild the region at the stored data version in a scratch graph, `GRAPH.COPY` it, delete the frozen accounts from the copy, and ask `shortestPath` for a route to every perimeter account again:

```cypher
MATCH (s:A {id: $o}) MATCH (t:A) WHERE t.id IN $perim
WITH t, shortestPath((s)-[:T*]->(t)) AS p RETURN t.id AS id, p IS NOT NULL AS reachable
// then on the copy:
MATCH (a:A) WHERE a.id IN $f DELETE a
// and the same shortestPath query again
```

All eight example plans pass, with nothing reachable afterwards. For the default alert, 021174:800737690, that is $22,410.20 that could move and three accounts to freeze.

I want to be plain about what this is. It is a consistency check on the same recorded transfers that produced the plan. It says that in those transfers no route is left. It is not evidence about the real world, and the card says so under the green chip.

It also broke in a boring way. `GRAPH.COPY` needs a fork, and the server refuses to fork while a background save is running, so the check failed at random. I turned RDB snapshots off (the append-only file already persists the data), added a retry with backoff, and as a last resort the code builds the second graph without the frozen accounts directly.

## A second check that does not share the plan's region

The copy check rebuilds the region the plan was computed on, so a mistake in building that region would be invisible to it. After a freeze I now run a second check that does not use the copy or the scratch graph: it re-expands the transfers from the source graph hop by hop, never stepping onto a frozen account, and asks whether any perimeter account was reached.

```cypher
MATCH (a:Account)-[t:TRANSFER]->(b:Account)
WHERE a.id IN $frontier AND NOT b.id IN $excluded AND t.ts >= $t_from AND t.ts <= $t_to AND t.batch <= $data_version
RETURN DISTINCT b.id AS id
```

On all eight examples the two checks agree: something reached the perimeter before the freeze, nothing after. Both still read the same recorded transfers, so this is a different route to the same data, not new evidence about the world.

![A green check: verified, no route left to the 3-hop perimeter, and $0 after the freeze](https://raw.githubusercontent.com/Hackmaass/Chokepoint/main/web/public/shots/s4-check.webp)

## What the graph buys, and the result that did not go my way

Before running the evaluation I wrote a protocol: laundering patterns with five or more transfers (155 of them), a seeded 50/50 split into dev and test, the configuration chosen on dev only, the test half run once. At equal budget I compared against rules an analyst would use without a graph, and scored what share of the laundering money that had already escaped each plan touched.

First what the graph buys, because it is easy to lose under the tie. The plan leaves no route to the perimeter in the recorded transfers, and it checks that before anyone acts. The accounts it freezes are more often laundering accounts: +0.19, interval [+0.07, +0.32]. Every frozen account sits on a route by construction, while 61% of the innocent accounts the simple rule picks have no route to the perimeter at all. And the rule cannot tell when it is finished: on a separate label-free run of 102 cases it needed more accounts than the minimum cut to leave no route in 64% of them, a median of 3 against 1. The cost side is not in the cut's favour either: it touches about five more legitimate transfers per case than the rule does.

What it did not buy is more blocked money. On the test half the cut blocked about the same share of escaped laundering money as "freeze the accounts the alerted account paid most recently": **difference -0.002, 95% interval [-0.09, +0.08].** On dev it had come out a few paired wins ahead of the strongest simple rule, so I expected more. That is a tie, and I wrote it down as one. The pitch I had planned, that the graph finds hidden chokepoints simple heuristics miss, is not supported by this evidence, so it is gone. I also broke my own protocol once: the lock rule asked for 50 dev cases with a route, and a half-sized dev set tops out at 46. I waived it and said so in the results file.

One more disclosure: the example alerts in the demo picker were chosen where the rule of thumb leaves money movable. The typical case is a tie. For the default alert, a same-size rule-of-thumb plan leaves about $19,300 of the $22,400 still movable, and that is a showcase, not an average.

## Money cannot go back in time

My plan treats the recorded transfers as a static map. That is conservative but it can over-warn, since it also cuts routes that run in the wrong order. So the card runs a timing check: start at the alerted account at the start of the window, keep only transfers that happen no earlier than the money could have arrived, and recompute.

On the 8 example alerts, 3 have no time-ordered route to the perimeter at all, 2 need fewer accounts, and 3 are unchanged, including the default alert. I added the check after the held-out run and it lives only on the card, so I claim nothing beyond those 8.

## A person approves

A checked plan can be approved by a named person, and the form lists the checks that apply before a name can be recorded: verified, current, actionable, no routes left, and the second check. The approval is written into the graph as a freeze order, bound to the plan, its accounts, the data version and the check by a SHA-256 hash.

The rule I care about is that an approval cannot outlive its evidence. It is refused for a plan that was not checked (`NOT_VERIFIED`), refused once newer transfers exist, and refused if the two checks disagree. It is an HTTP endpoint, not an agent tool, so the model cannot call it. No bank is called either; a person would send the order on.

![A receipt: the finding, the tool run that produced it, its parameters, the data version and the accounts it read](https://raw.githubusercontent.com/Hackmaass/Chokepoint/main/web/public/shots/s5-receipt.webp)

The cost weight has one more use. In the evaluation it mostly acted as a tie-breaker, so I did not build the story around it. The card still offers it: on 2 of the 8 examples, including the default alert, a gentler plan exists (3 accounts and 21 everyday transfers become 5 accounts and 6), each option is checked before it can be approved, and it is a trade-off shown, not a claim that more accounts is better. It was not part of the held-out run.

## Late data

When late or back-dated transfers arrive, each saved finding is linked by `READ` edges to the accounts its tool run looked at. Which findings are now wrong becomes one traversal, and only those are re-run. On 40 held-out cases with four kinds of late batch, the refreshed state equalled a full recompute in 40 of 40, and the scope rule missed no change. It re-ran 80 of 160 findings.

The saving is real only when the late data misses the case: touch any account a case read and all four findings re-run. The batches were synthetic.

## Check the numbers yourself

One command re-measures the figures in this post, with no key and no model call:

```bash
docker exec chokepoint-api python scripts/judge_check.py
```

It re-checks the eight plans, the second check, the timing check, the 11-edge repro, the certificate and the approval gate, and exits non-zero if a measured line fails. The held-out numbers are printed as RECORDED and never as PASS, because they need the full dataset and the original protocol.

## What I am not claiming

The data is synthetic. It has no balances, so there are no partial freezes, and "traced in" is an upper bound rather than a share of anything. The plan is static; only the card's check is time-aware. Nothing here promises anything for real data. The public demo replays a saved agent run rather than answering new questions.

An AI coding assistant, Claude Code, wrote most of the code and docs under my direction; [docs/ai-work-log.md](https://github.com/Hackmaass/Chokepoint/blob/main/docs/ai-work-log.md) lists what it did and what I have reviewed. The agent inside the product is a free NVIDIA Nemotron model. It chooses tools and writes a short summary, and every figure on a card comes from a tool, never the model. That separation exists because the model slipped: across 120 evaluated runs one summary quoted a dollar figure no tool had returned, and the interface now flags that.

## Try it

- Live demo: https://chokepoint-uou2.onrender.com (replays saved runs and runs the tools on the 8 example alerts; a free host, so the first load can take a minute)
- Repository: https://github.com/Hackmaass/Chokepoint
- Video: https://REPLACE-WITH-VIDEO-URL
