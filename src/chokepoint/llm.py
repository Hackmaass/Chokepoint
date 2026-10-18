"""Provider-neutral chat client for OpenAI-compatible endpoints (free NVIDIA Nemotron by default).

- App-side rate limit (default 30 requests/minute, below the reported free-tier limit).
- Record/replay cache keyed by the request body: ``live`` records, ``replay`` serves recorded answers and fails
  loudly on a miss, ``off`` disables the cache. Anything served from the cache is flagged ``cached=True`` so the
  UI can label it.
- One retry on an empty reply (no text and no tool call), the single failure seen in the tool-call probe.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import PROJECT_ROOT, Settings, get_settings

CACHE_DIR = PROJECT_ROOT / "llm_cache"  # live runs (git-ignored)
REPLAY_DIR = PROJECT_ROOT / "replays"  # saved demo runs (committed)


class LLMError(Exception):
    pass


class ReplayMiss(LLMError):
    pass


@dataclass
class ChatResult:
    message: dict
    cached: bool
    latency_s: float


class LLMClient:
    def __init__(self, settings: Settings | None = None, mode: str = "live", cache_dir: Path | None = None,
                 temperature: float = 1.0, top_p: float = 0.95, max_tokens: int = 4096, attempts: int = 5):
        self.s = settings or get_settings()
        if mode not in ("live", "replay", "off"):
            raise ValueError("mode must be live, replay or off")
        self.mode, self.cache_dir = mode, cache_dir or CACHE_DIR
        self.temperature, self.top_p, self.max_tokens = temperature, top_p, max_tokens
        self.attempts = attempts  # a public demo gives up sooner, so it can fall back to a saved run
        self._lock = threading.Lock()
        self._last = 0.0
        self._client = httpx.Client(timeout=120)

    def _key(self, body: dict) -> Path:
        return self.cache_dir / (hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24] + ".json")

    def _post(self, body: dict) -> dict:
        if not self.s.llm_api_key:
            raise LLMError("no LLM API key configured (NVIDIA_API_KEY / LLM_API_KEY)")
        gap = 60.0 / max(1, self.s.llm_rpm_limit)
        for attempt in range(self.attempts):
            with self._lock:
                wait = self._last + gap - time.time()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                r = self._client.post(f"{self.s.llm_base_url}/chat/completions", json=body,
                                      headers={"Authorization": f"Bearer {self.s.llm_api_key}"})
            except httpx.TransportError:  # timeout, connection reset, DNS: retry with backoff
                time.sleep(min(30, 3 * 2**attempt))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 4 * 2**attempt))
                continue
            if r.status_code >= 400:
                raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        raise LLMError("LLM unavailable after retries (rate limited, timeouts or server errors)")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, tool_choice: str = "auto") -> ChatResult:
        """One model turn. A reply that is empty, or cut off by the token limit (the model spends part of the
        budget on hidden reasoning), is retried once with twice the budget."""
        t0 = time.time()
        last: ChatResult | None = None
        for attempt in range(2):
            body = {"model": self.s.llm_model, "messages": messages, "temperature": self.temperature,
                    "top_p": self.top_p, "max_tokens": self.max_tokens * (2**attempt)}
            if tools:
                body.update(tools=tools, tool_choice=tool_choice)
            path = self._key(body)
            if self.mode != "off" and path.exists():
                data, cached = json.loads(path.read_text(encoding="utf-8")), True
            elif self.mode == "replay":
                raise ReplayMiss(f"no recorded answer for this request ({path.name})")
            else:
                data, cached = self._post(body), False
                if self.mode == "live":
                    self.cache_dir.mkdir(exist_ok=True)
                    path.write_text(json.dumps(data), encoding="utf-8")
            choice = data["choices"][0]
            msg = choice["message"]
            last = ChatResult(msg, cached, time.time() - t0)
            has_call = bool(msg.get("tool_calls"))
            has_text = bool((msg.get("content") or "").strip())
            if has_call or (has_text and choice.get("finish_reason") != "length"):
                return last
        return last or ChatResult({"role": "assistant", "content": ""}, False, time.time() - t0)
