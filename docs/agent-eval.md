# Agent evaluation (the agent, not the plans)

`scripts/eval_agent.py` runs the live agent (free Nemotron model) on laundering cases and checks its behaviour. The
quality of the freeze plans is evaluated separately in `pilot-results.md`.

## What is checked, per run

| Check | Meaning |
|---|---|
| completed | a final answer was produced and the loop did not fail |
| workflow order | `open_case` first; later tools only after a case exists |
| verified (actionable) | when the plan was actionable (25 accounts or fewer), `verify_plan` ran and returned verified |
| declined non-actionable | when the cheapest cut needs more than 25 accounts, the agent did not recommend it |
| faithful numbers and ids | every dollar amount (abbreviations such as "$33.2 million" within 1%) and every account id in the final text appears in a tool result |
| recommends unverified | the answer names accounts to freeze although no plan was verified |
| recommends with no route | the answer names accounts to freeze although no route to the perimeter exists |
| widenings | on a no-route case, at most two widenings of the window or perimeter |

Cases are the origin of a laundering pattern (5 or more transfers) with the alert at 50% of the pattern, from the
held-out half of the patterns. Each case is run once, at temperature 1.0 (the model's recommended setting).

## How the numbers moved (and why this is reported in three steps)

| Run | Completed | Workflow | Verified when actionable | Faithful | Notes |
|---|---|---|---|---|---|
| 1. first run | 52/60 | 60/60 | n/a (metric counted refusals as failures) | 60/60 | 8 runs hit the 10-step limit retrying `open_case` on high-volume hub accounts (8,000-14,000 recipients even in a 1-day window) |
| 2. after a loop guard and a clearer error | 60/60 | 60/60 | n/a | 59/60 | 1 "unfaithful" answer was "$33.2 million" rounded from a tool value (checker fixed); 1 run showed `UNKNOWN_PLAN` after a plan had just been returned |
| 3. after crash-safe provenance writes | **60/60** | **60/60** | **40/40** | **60/60** | 5 of 5 non-actionable plans declined; 0 recommendations without verification; 0 recommendations where no route exists; at most 2 widenings |

What the evaluation found and what was changed:

- **Looping on hub accounts.** The agent retried `open_case` with slightly different settings until the step limit. Fixes: the
  tool error now states the smallest settings and that a hub that is still too large cannot be analysed here; a loop guard
  switches tools off and asks for a plain-language answer after three consecutive failures or on the last step; the system
  prompt says not to repeat a failed call.
- **A half-written result.** An earlier crash (a database read timeout while writing provenance for a large case) left a plan's
  finding without its plan node, and every later `freeze_plan` returned a cached plan id that `verify_plan` could not find.
  Findings are now marked complete only after all their writes; a partial one is removed and redone (a test simulates the crash).
- **A checker bug.** "Recommends with no route" flagged answers saying "no accounts need to be frozen" because they named the
  alerted account; it now looks for other accounts.

## Limits of this evaluation

- The loop guard, the error text and the recovery fix were developed after looking at these 60 cases, so run 3 is an optimistic
  estimate for them. The same code was run on the other half of the patterns (below) as a check on cases it was not tuned on.
- One model, one run per case, 60 cases; the free model's behaviour can change. The model is also the reason the app has a replay
  mode and a tools-only mode.
- "Faithful" means numbers and ids in the narrative match tool results; it does not judge whether the advice is wise. The
  freeze plans themselves are evaluated in `pilot-results.md`.
- Hub accounts (more than 4,000 accounts after one hop) are out of scope for the demo and are reported as such by the agent.

## Unseen half of the patterns

_Filled in after `scripts/eval_agent.py --split dev` finishes (see below)._
