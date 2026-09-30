"""Skip a model for the rest of a scan once it has failed K tickers in a row.

Tenacity decides whether one REQUEST is worth retrying; this decides whether a
MODEL is worth asking at all. On 2026-09-24 every Gemini request failed, and
each of 38 tickers paid primary x3 -> fallback x3 before reaching DeepSeek --
about 150 wasted requests and ten minutes of back-off, with the fallback's
20 RPD spent on 503s before any ticker could use it. On 2026-09-25 retrying
helped, so the unit is K CONSECUTIVE failures of one model, and a success
resets it.

The scope is one scan: `run_scan` / `run_scan_etf` each build a breaker, so a
model tripped today is asked again tomorrow. It is the news chain's per-provider
day breaker, scoped to a scan and keyed per (provider, MODEL) because Google
meters per model and both Gemini tiers are provider 'gemini'.

Only a failed REQUEST counts (the exception out of `_call_api`, after its
retries). A parse error means the model answered: it is available, and the
fallback-on-ValueError path already handles the answer.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class ModelSkipped(RuntimeError):
    """Raised instead of a request when a model's breaker is open."""


class ModelBreaker:
    def __init__(self, threshold: int):
        # threshold <= 0 disables: every model is always asked.
        self.threshold = threshold
        self._streak: dict[tuple[str, str], int] = {}
        self.tripped: list[tuple[str, str]] = []
        # Scans are serial, but each analyst call runs in asyncio.to_thread.
        self._lock = threading.Lock()

    def is_open(self, provider: str, model: str) -> bool:
        with self._lock:
            return (provider, model) in self.tripped

    def record_success(self, provider: str, model: str) -> None:
        with self._lock:
            self._streak[(provider, model)] = 0

    def record_failure(self, provider: str, model: str) -> None:
        key = (provider, model)
        with self._lock:
            self._streak[key] = self._streak.get(key, 0) + 1
            if (self.threshold > 0 and self._streak[key] >= self.threshold
                    and key not in self.tripped):
                self.tripped.append(key)
                logger.warning(
                    "Analyst model '%s'/'%s' failed %d tickers in a row -- "
                    "skipping it for the rest of this scan",
                    provider, model, self._streak[key],
                )
