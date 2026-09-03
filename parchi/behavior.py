"""The patterns one cart alone cannot show.

The checks in `checks.py` judge one cart against one mandate. This module asks
what the *sequence* of attempts says: an attacker who cannot guess where the
wall is buys one thing at a time. Three detectors, each reporting a pattern,
and none of them able to change a verdict. That is the enforcement/detection
split the rest of this codebase keeps.

1. Purchase burst: repeated attempts (allowed or refused) from one actor inside
   a short window. Individual verdicts can all be correct; the *rate* is the
   signal. A bot enumerating items, testing stolen instruments, or reselling
   wants many purchases, not a perfect one.

2. Coupon farming: the same discount code sweeping through many different
   mandates. Farming looks identical to a popular coupon right up until you
   count mandates; a store-wide sale is everyone's cart, farming is one actor's
   many mandates. Two escalation tiers: is this code hot right now, and has
   this code been used across mandates far beyond what one payer should produce.

3. Discount drift: the same code presented twice with *different claimed
   values*. The engine verifies each cart in isolation, so a code worth Rs 100
   claimed at Rs 100 in one cart is verified true, and the same code claimed at
   Rs 900 in another cart is verified false, and both records are correct.
   What nobody looks at is that one code paying out three different amounts is
   not three verdicts, it is an enumeration pattern targeting the coupon rail.
   The alert names the code, the total drift and each observed value, and fires
   only on the first drift per code, so it is one alert per discovery rather
   than one per attempt.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from .mandate import Cart, IntentMandate, norm

CRITICAL = "critical"
HIGH = "high"


@dataclass(frozen=True)
class Pattern:
    """A named behaviour, ready for `raise_alert`.

    `kind` has to be unique in `threat.py`'s universe: the console deduplicates
    by kind when it draws "what is being attempted", so a reused name would
    quietly merge two different attacks into one bar.
    """

    kind: str
    severity: str
    summary: str
    detail: str
    actor: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "severity": self.severity, "summary": self.summary,
                "detail": self.detail, "actor": self.actor}


class BurstDetector:
    """Many attempts from one actor inside a window, allowed or refused.

    `ProbeDetector` in `threat.py` counts *refused* attempts: someone mapping
    the wall. This one counts *all* attempts, refused or allowed, because the
    attack shape is different. A bot enumerating stock, testing a list of
    stolen instruments, or reselling wants volume, and every ALLOW it extracts
    is real money out the door. The perfect individual verdicts are exactly
    why nothing else would notice.

    Threshold 8 in 60s: a human buying repeatedly through an agent session is
    plausible; a human making 8 purchases a minute through an agent is not.
    """

    def __init__(self, threshold: int = 8, window_seconds: int = 60) -> None:
        self.threshold = threshold
        self.window = window_seconds
        self._seen: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def record(self, actor: str, now: float | None = None) -> int:
        """Register an attempt. Returns how many are inside the window."""
        now = time.time() if now is None else now
        with self._lock:
            hits = self._seen.setdefault(actor, deque())
            hits.append(now)
            while hits and now - hits[0] > self.window:
                hits.popleft()
            return len(hits)

    def is_burst(self, count: int) -> bool:
        return count >= self.threshold

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


class CouponWatcher:
    """How one discount code is being used across mandates, over time.

    Sees every attempt that names a code, including the ones the discount
    check refuses. A block is the engine saying "this attempt is wrong"; the
    watcher is the part that says "and this is the fourth wrong attempt at the
    same code, which is not a typo any more".

    In-memory and per-process, like every other detector here. A real
    deployment backs this with shared state; the interface is the part that
    survives.
    """

    def __init__(self, hot_threshold: int = 5, hot_window_seconds: int = 120,
                 max_mandates_per_code: int = 12) -> None:
        self.hot_threshold = hot_threshold
        self.hot_window = hot_window_seconds
        self.max_mandates = max_mandates_per_code
        # code -> deque of (ts, actor, mandate_id, claimed_paise)
        self._events: dict[str, deque[tuple[float, str, str, int]]] = {}
        # codes that already raised their per-code alert, by alert kind
        self._raised: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def _prune(self, code: str, now: float) -> None:
        events = self._events.get(code)
        if events:
            while events and now - events[0][0] > self.hot_window:
                events.popleft()

    def observe(self, cart: Cart, mandate: IntentMandate | None = None,
                now: float | None = None) -> list[Pattern]:
        """Record one attempt and return any patterns it completed.

        A single attempt can complete more than one pattern, because the
        eighth reference inside the hot window is usually also a new mandate
        number. This returns a list, and the caller raises everything in it.
        """
        now = time.time() if now is None else now
        code = norm(cart.discount_code or "")
        if not code:
            return []

        actor = (cart.agent_id if cart.agent_id else
                 (mandate.payer_id if mandate else "unknown")) or "unknown"
        mandate_id = mandate.mandate_id if mandate else ""
        claimed = int(cart.discount_paise or 0)

        patterns: list[Pattern] = []
        with self._lock:
            events = self._events.setdefault(code, deque())
            events.append((now, actor, mandate_id, claimed))
            self._prune(code, now)
            raised = self._raised.setdefault(code, set())

            # Tier 1: the code is being attempted far too often right now.
            # Every event inside the window counts, blocked or allowed: a
            # burst of refused attempts is enumeration in progress.
            if len(events) >= self.hot_threshold and "coupon_hot" not in raised:
                raised.add("coupon_hot")
                patterns.append(Pattern(
                    "coupon_hot", HIGH,
                    f"Discount code '{code}' attempted {len(events)} times in "
                    f"{self.hot_window}s",
                    f"{len(events)} attempts inside {self.hot_window}s, blocked "
                    "attempts included. A checkout problem retries once or twice; "
                    "a burst like this is a script working the coupon rail.",
                    actor))

            # Tier 2: mandates. The count that separates farming from a popular
            # coupon. Two actors sharing a code is a sale; one code spread over
            # dozens of distinct mandates is harvested permission slips.
            distinct = {m for (_, _, m, _) in events if m}
            if len(distinct) >= self.max_mandates and "coupon_farming" not in raised:
                raised.add("coupon_farming")
                patterns.append(Pattern(
                    "coupon_farming", CRITICAL,
                    f"Code '{code}' used across {len(distinct)} different mandates",
                    f"{len(distinct)} distinct mandates inside {self.hot_window}s "
                    f"carried code '{code}'. A store-wide sale is many payers on "
                    "one code. This shape, one code and mandate after mandate, is "
                    "harvested slips being spent.", actor))

        return patterns

    def observe_claimed_value(self, code: str, claimed_paise: int,
                              now: float | None = None) -> Pattern | None:
        """Detect the same code presented with different claimed values.

        The per-cart discount check already refuses any single wrong claim;
        this is the cross-record view no single cart can produce. Fires once
        per code, on the first drift, so the console is told about the
        enumeration pattern once rather than for every attempt of it.
        """
        now = time.time() if now is None else now
        code = norm(code or "")
        if not code:
            return None
        with self._lock:
            seen: dict[int, float] = {}
            for ts, _actor, _mid, claimed in self._events.get(code, ()):
                seen.setdefault(claimed, ts)
            if len(seen) < 2 or "discount_drift" in self._raised.setdefault(code, set()):
                return None
            self._raised[code].add("discount_drift")
            values = sorted(seen, key=lambda v: (seen[v], v))
            spread = max(values) - min(values)
            return Pattern(
                "discount_drift", HIGH,
                f"Code '{code}' claimed at different values across attempts",
                f"Code '{code}' claimed at "
                + ", ".join(f"Rs {v / 100:.2f}" for v in values)
                + f", a spread of Rs {spread / 100:.2f}. One code paying "
                f"{len(values)} different amounts is an enumeration pattern "
                "against the coupon rail.",
                "unknown")

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._raised.clear()


def check_patterns(cart: Cart, mandate: IntentMandate | None,
                   burst: BurstDetector, coupons: CouponWatcher,
                   verdict: str, now: float | None = None) -> list[Pattern]:
    """Run every behavioural detector for one authorization attempt.

    Called after the verdict, like `threat.classify`: nothing in here can
    change what was decided, it only decides who hears about it. Returns every
    pattern the attempt completed, since a burst threshold can be crossed by the
    same attempt that crosses a coupon threshold.
    """
    now = time.time() if now is None else now
    patterns: list[Pattern] = []

    actor = (cart.agent_id if cart.agent_id else
             (mandate.payer_id if mandate else "unknown")) or "unknown"

    # Every attempt counts, allowed or refused: the volume is the signal. The
    # alert fires at the moment the threshold is crossed, not on every attempt
    # after it - a standing burst would otherwise raise one alert per click
    # until the window slides.
    count = burst.record(actor, now)
    if burst.is_burst(count) and count == burst.threshold:
        patterns.append(Pattern(
            "purchase_burst", HIGH,
            f"{count} purchase attempts from '{actor}' in under a minute",
            "Allowed and refused attempts together. A bot enumerating stock, "
            "testing stolen instruments or reselling wants volume, not "
            "perfection, and every ALLOW in a burst is real money out the door "
            "with an individually correct verdict behind it.", actor))

    patterns.extend(coupons.observe(cart, mandate, now))
    drift = coupons.observe_claimed_value(cart.discount_code,
                                          int(cart.discount_paise or 0), now)
    if drift:
        patterns.append(drift)

    # Suppression guard: a drift alert arriving in the same breath as a hot or
    # farming alert on the same code is one incident read twice. Keep the code
    # alert, which names the abuse more precisely, and drop the drift echo.
    kinds = {p.kind for p in patterns}
    if "discount_drift" in kinds and ("coupon_hot" in kinds or "coupon_farming" in kinds):
        patterns = [p for p in patterns if p.kind != "discount_drift"]
    return patterns
