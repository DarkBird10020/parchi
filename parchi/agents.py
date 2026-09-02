"""Agent identity: the missing piece of "my agent did that, I didn't".

Parchi verifies that a cart is inside the signed payer intent. It also needs to
know *which agent* is presenting the cart: a stolen agent credential should not
be able to replay a valid mandate.

This module is a deliberately simple registry. A production deployment would be
a shared store; the interface is what would survive.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
)


class AgentRegistry:
    """In-memory mapping from agent_id to public key."""

    def __init__(self) -> None:
        self._keys: dict[str, Ed25519PublicKey] = {}

    def register(self, agent_id: str, pub: Ed25519PublicKey) -> None:
        self._keys[agent_id] = pub

    def get(self, agent_id: str) -> Ed25519PublicKey | None:
        return self._keys.get(agent_id)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._keys
