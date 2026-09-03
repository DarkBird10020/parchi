"""The AI adjudicator: is this account actually under attack?

`behavior.py` counts things: attempts, codes, mandates. A counter cannot read
a situation: it cannot tell a flash sale from a bot farm, a reseller from a
card-tester, or a family sharing one account from an agent swarm. This module
answers that question with one model call over the account's recent history.

The judgement gate, not the enforcement gate
--------------------------------------------
This answers "should we escalate", never the engine's "does this cart pass".
A wrong call here costs a ten-minute cooldown that a human releases in the
console, never a silently stolen purchase. That asymmetry is written into the
prompt itself, because measuring it showed the model does not assume it: the
first version of this prompt convicted 15 of 18 ordinary customers, and telling
it what a false conviction costs took that to 2 of 18 without losing a single
attack.

Fail-open, on purpose, in one direction only
--------------------------------------------
If the model is unavailable, degraded, or out of budget, the adjudicator says
`None` and the ratchet does what its plain rules already did. It never says
"block because I could not think", because the failure of an *opinion* must not
become a *punishment*.

Model choice
------------
`PARCHI_GUARD_MODEL` pins the adjudicator's model explicitly; left alone it
resolves `DEFAULT_GUARD_MODEL` against the endpoint's live catalogue exactly
like the intent check, so a retired name falls through instead of 404-ing.
Whatever runs here should be scored by `eval/adjudicator.py` first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from . import openai_provider

# The default is a measurement, not a preference. `eval/adjudicator.py` scores
# whatever runs here against twelve labelled situations, half of them ordinary
# customers, and this model scored 18/18 attacks with 16/18 customers left
# alone at ~2s a call.
#
# It is not the biggest model available, and that was the finding. The
# 5-series tier was pinned here first on the theory that a harder judgement
# deserves a heavier model; it then started answering HTTP 402 (the account's
# credit for that tier), and the flagship `TEE/glm-5.2` took 70s per call and
# returned a confidence of 7 on a 0-1 scale. A model that is not answering is
# not adjudicating, so the default is the one that is measured, available and
# fast. `PARCHI_GUARD_MODEL` pins any other.
DEFAULT_GUARD_MODEL = "z-ai/glm-4.7-flash"

# How sure the adjudicator has to be before an account is cooled down. Named
# here rather than written into the caller, because this number is the price of
# a false conviction: below it the model's opinion is recorded and nobody is
# blocked, at or above it a real customer loses ten minutes. `eval/adjudicator.py`
# scores the gate at this value.
CONFIDENCE_GATE = 0.6

SCHEMA = {
    "type": "object",
    "properties": {
        "attack": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["attack", "confidence", "reason"],
    "additionalProperties": False,
}

PROMPT = """You are the risk reviewer inside a payment checkpoint. Automated
counters fired on one account. Decide whether the account is being ATTACKED or
merely being USED in a way that tripped a counter.

The cost of being wrong is not symmetric, and that asymmetry is your instruction:
- Convicting a real customer blocks their account for ten minutes. They are
  mid-purchase and they did nothing wrong.
- Clearing a real attacker costs nothing here. Every deterministic refusal still
  stands, the alert still reaches a human, and the money that was going to be
  refused is still refused.

So: clear the account unless the evidence has no ordinary explanation. Do not
convict on volume alone. Ask "what would an ordinary customer look like here?"
and convict only when no such story fits the facts you were given.

What actually separates the two
-------------------------------
Coupon use: count PAYERS, not attempts. Many different payers on one code is a
public sale, however hot it looks. One payer sweeping a code across many
mandates is farming. Most coupon cases never reach you at all, because the ones
that can be settled by counting are settled before this is called.

Repeated attempts: count what CHANGED, and check what happened last time.
The same cart resubmitted after no verdict, or after it was allowed, is a retry
loop: a flaky connection, not an attack. The same cart resubmitted after it was
REFUSED, with only the nonce changed, is a rebuild, and that has no innocent
version: a real customer whose purchase was refused changes the purchase, not
the serial number on the request. Many DIFFERENT items in quick succession on
one mandate is a catalogue sweep. Many different payment instruments on cheap
items is card testing.

Item variety alone is not an attack. Gift shopping, an office order and a
restock all look like several unrelated items bought quickly by one person.

The one shape with no innocent version: several distinct AGENT credentials
presenting slips for ONE payer. An account here is operated through its own
registered agent, so a person buying gifts, buying for a team, or reselling
still shops through one agent. Many agent identities on one wallet is a
credential farm. Convict that even when every individual cart looks ordinary.

A code claimed at DIFFERENT values across attempts also has no innocent
version: it is enumeration of the coupon rail.

Evidence
--------
{evidence}

Reply with JSON only: {"attack": true|false, "confidence": 0.0-1.0, "reason":
"<one or two sentences naming what the pattern most likely is>"}"""


@dataclass(frozen=True)
class AttackAssessment:
    """The adjudicator's answer, or the absence of one."""

    attack: bool
    confidence: float
    reason: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {"attack": self.attack, "confidence": round(self.confidence, 2),
                "reason": self.reason, "model": self.model}


def _build_evidence(actor: str, signals: dict[str, Any]) -> str:
    """The facts, as compact labelled lines a model can read quickly.

    Everything here is produced by the deterministic detectors; the model is
    shown no free text from the attacker beyond line descriptions, which the
    intent-check experience already showed can carry injection. Cap what is
    shown.
    """
    lines = [f"account/agent id: {actor}"]
    for key, value in signals.items():
        if isinstance(value, list):
            shown = ", ".join(str(v)[:60] for v in value[:8])
            lines.append(f"{key}: {shown}")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines[:25])


def assess_attack(actor: str, signals: dict[str, Any],
                  timeout: float = 30.0,
                  model: str | None = None) -> AttackAssessment | None:
    """One model call: attack, or not. `None` when it cannot judge.

    `signals` is whatever the deterministic detectors measured for this actor:
    burst counts, coupon codes and their claimed values, mandate counts, line
    descriptions. A wrong call here is an unnecessary cooldown, never a stolen
    purchase, so the model is allowed to be an opinion.
    """
    try:
        openai_provider.load_dotenv()
        chosen = (model or os.environ.get("PARCHI_GUARD_MODEL")
                  or openai_provider.resolve_model(DEFAULT_GUARD_MODEL))
        prompt = PROMPT.replace("{evidence}", _build_evidence(actor, signals))
        # Three attempts: the pinned tier twice, then the intent check's fast
        # flash model, which has proven the most reliable model on this
        # endpoint. The intent check forbids retries because it sits inside a
        # payment decision; this call gates a ten-minute cooldown, where one
        # clean re-ask is cheaper than losing the judgement to endpoint noise.
        # A fallback model still answers the same question from the same
        # evidence - the tier is a preference, the verdict is the product.
        attempts = [chosen, chosen]
        fallback = "z-ai/glm-4.7-flash"
        if chosen != fallback:
            attempts.append(fallback)
        d = None
        last_error: Exception | None = None
        for attempt_model in attempts:
            try:
                d = openai_provider.chat_json_schema(
                    prompt, SCHEMA, ("attack", "confidence", "reason"),
                    timeout, attempt_model, max_tokens=300,
                    name="attack_assessment")
                chosen = attempt_model
                break
            except Exception as exc:
                last_error = exc
        if d is None:
            raise last_error if last_error else RuntimeError("adjudication failed")
        if type(d.get("attack")) is not bool:
            return None
        conf = d.get("confidence")
        # The range check has already caught a real failure: GLM-5.2 returned
        # `confidence: 7` on a 0-1 scale. An uninterpretable verdict is no
        # verdict, and no verdict never blocks anyone.
        if not isinstance(conf, (int, float)) or not 0.0 <= float(conf) <= 1.0:
            return None
        reason = str(d.get("reason", "")).strip()
        if not reason:
            return None
        return AttackAssessment(bool(d["attack"]), float(conf), reason[:300], chosen)
    except Exception:
        # Unavailable, unconfigured, degraded, out of budget: an opinion that
        # cannot be given must never become a punishment. The ratchet's plain
        # thresholds still apply on their own.
        return None
