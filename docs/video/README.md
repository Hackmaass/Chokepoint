# Demo video, recut on 2026-10-19 (late-submission window)

**File:** [`chokepoint-demo-recut.mp4`](chokepoint-demo-recut.mp4) (1920x1080, about 2:40). GitHub does not play repository videos inline: use *Download* or *View raw*.
The on-time video (linked from the main README) is the original, uploaded to YouTube before the deadline; this recut shows what was added afterwards.

**What it shows** (every number is one the tools print for the default example alert, `021174:800737690`):
the alert, the agent's case, the plan ($22,410 ceiling, 3 accounts, "not a forecast"), the check on a copy of the graph and the second check on the source graph,
FalkorDB's `algo.maxFlow` returning 300 where the true maximum is 453 and the repair inside FalkorDB, the honest comparison with the rule of thumb (about the same blocked share;
the example was picked because the rule leaves money movable; the typical result is a tie), the human approval and the Dispatcher (holds and a filed freeze request),
the hash-chained receipts re-verified in the browser, and the public MCP endpoint.

**Disclosures**
- **Voice:** AI-generated, the Microsoft Edge neural voice `en-US-AndrewNeural` (text-to-speech, not the author's voice). Delivery was modulated phrase by phrase (rate and pitch per sentence, pauses between thoughts); the settings are in [`chokepoint-recut-script.json`](chokepoint-recut-script.json).
  Pronunciation was checked by transcribing each beat with a speech recogniser and diffing it against the script (no word was lost); the author has not yet listened to it.
- **GitHub step:** in this recording the freeze request goes to a *local stand-in* for GitHub (the video says so on screen and in the voice-over), not to a real repository.
- **Footage:** a real run of the app in Replay mode (saved agent run, no model call) on synthetic data; nothing is mocked except that GitHub stand-in.
- **Code and tooling:** written with Claude Code under the author's direction (the script was reviewed with Claude Code's advisor tool). The video tooling (Remotion composition, recorder, voice scripts) lives outside this repository.

**Script and delivery:** see [`chokepoint-recut-script.json`](chokepoint-recut-script.json) (`say` is what the voice is given, spelled for pronunciation; `captions` maps it back to the real spelling on screen).

A YouTube upload of the recut is the author's to do; until then this file is the recut.
