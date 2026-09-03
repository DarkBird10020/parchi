"""Automatic cooldown: 10 minutes, then a human decides.

`behavior.py` names patterns. Most of them are worth an alert and nothing more,
because a counter cannot tell a busy customer from a bot. Three shapes earn the
cooldown, and they arrive by two different routes.

**Settled by counting, no model involved.**

- *A coupon claimed at more than one value.* A coupon is worth what it is
  worth. No sale, retry or honest mistake makes one code pay two different
  sums, so there is nothing here to weigh up.
- *A coupon being farmed.* One payer carrying a code across many mandates is
  farming whatever the code is; many payers on a code the merchant issued to
  one named customer means it has leaked. Both are arithmetic plus a lookup in
  the merchant's own book, so `coupon_verdict` decides them and no model is
  asked. Handing a model countable facts is how the spending cap ended up being
  re-decided in FAILURES entry 10, and a decision table in the prompt cost four
  of eight attacks before it was taken back out.

**Sent to the adjudicator, because counting cannot settle it.**

- *Swarm.* Many agent ids presenting mandates that all name the same payer is
  one account wearing many faces. Agents are registered credentials, so the
  legitimate version of "my household shares one login" does not exist here,
  and one hijacked payer key fanned out across a farm of agents is the shape of
  a stolen-key harvest. What a counter cannot tell you is whether this
  particular fan-out is a farm or an integration doing something odd, so
  `ai_guard.py` reads it and the ratchet triggers only on its verdict at or
  above the confidence gate. An unavailable model blocks nobody: the alert
  still reaches a human and the deterministic refusals still stand.

The cooldown is enforced before the engine runs: it short-circuits, and no
money moves from a cooled account. The operator sees a critical alert with a
release button, because an automatic block with no human release is a lockout
nobody can undo at 3am, and the release is logged with their name on it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

COOLDOWN_SECONDS = 600          # 10 minutes, per the operator's request
SWARM_AGENT_THRESHOLD = 3       # more than this many agents on one payer


@dataclass(frozen=True)
class CooldownState:
    active: bool
    reason: str | None = None
    seconds_left: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"active": self.active, "reason": self.reason,
                "seconds_left": self.seconds_left,
                "ends_at_ms": self.ends_at_ms}

    @property
    def ends_at_ms(self) -> int | None:
        """Wall-clock end of the block, so a browser can count it down.

        Computed rather than stored: the state is derived from `now` anyway,
        and an absolute end survives a page reload, unlike a countdown.
        """
        if not self.active:
            return None
        return int((time.time() + self.seconds_left) * 1000)


class CooldownStore:
    """Who is cooling down, why, and until when. In-memory, per process."""

    def __init__(self, cooldown_seconds: int = COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = cooldown_seconds
        # key -> {"until": ts, "reason": str, "assessment": dict|None}
        self._active: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def check(self, payer_id: str, agent_id: str = "",
              now: float | None = None) -> CooldownState:
        """Is this actor currently cooling down?

        The payer is the account that loses money, so a cooldown on the payer
        covers every agent presenting its slips, the swarm case included.
        An agent-only key would let the farm rotate to a fresh agent id and
        walk straight back in.
        """
        now = time.time() if now is None else now
        with self._lock:
            entry = self._active.get(payer_id)
            if entry is None:
                return CooldownState(False)
            left = int(entry["until"] - now)
            if left <= 0:
                # Expired: the window passed and nobody intervened. Release.
                del self._active[payer_id]
                return CooldownState(False)
            return CooldownState(True, entry["reason"], left)

    def trigger(self, payer_id: str, reason: str,
                assessment: dict[str, Any] | None = None,
                now: float | None = None) -> CooldownState:
        """Start or extend a cooldown. Returns the state it produced."""
        now = time.time() if now is None else now
        with self._lock:
            self._active[payer_id] = {
                "until": now + self.cooldown_seconds,
                "reason": reason,
                "assessment": assessment,
                "started": now,
            }
            return CooldownState(True, reason, self.cooldown_seconds)

    def release(self, payer_id: str, now: float | None = None) -> bool:
        """The operator's button. Returns whether anything was actually held."""
        now = time.time() if now is None else now
        with self._lock:
            entry = self._active.get(payer_id)
            if entry is None:
                return False
            del self._active[payer_id]
            return True

    def held(self, now: float | None = None) -> dict[str, dict[str, Any]]:
        """Everything currently cooling down, for the console's release panel."""
        now = time.time() if now is None else now
        with self._lock:
            out = {}
            for key, entry in list(self._active.items()):
                left = int(entry["until"] - now)
                if left <= 0:
                    del self._active[key]
                    continue
                out[key] = {"reason": entry["reason"],
                            "seconds_left": left,
                            "assessment": entry.get("assessment"),
                            "started": entry.get("started")}
            return out

    def reset(self) -> None:
        with self._lock:
            self._active.clear()


def detect_swarm(mandate_payer_id: str, cart_agent_id: str,
                 seen: dict[str, set[str]]) -> bool:
    """Record this attempt and say whether the payer crossed the swarm line.

    `seen` maps payer_id -> the set of agent ids that have presented its slips
    inside the current window; the caller owns the window. Three or more
    distinct agents naming one payer is the farm shape. A registered agent is
    a credential, so "many faces, one wallet" is not a household sharing a
    login, it is a key being worked by a farm.

    True fires on each NEW face at or above the line: a repeat by an agent
    already counted is a return, not news, while a fresh credential on a payer
    that is already swarming is exactly when the block should be extended.
    """
    if not mandate_payer_id or not cart_agent_id:
        return False
    agents = seen.setdefault(mandate_payer_id, set())
    before = len(agents)
    agents.add(cart_agent_id)
    grew = len(agents) != before
    return len(agents) >= SWARM_AGENT_THRESHOLD and grew
