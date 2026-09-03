"""The adjudicator's refusal to answer, which is most of its job.

`eval/adjudicator.py` scores whether the verdicts are *right*, and needs a key
to do it. These tests need no key, because they cover the half that must never
depend on a network: every way a model can answer badly has to end as "no
verdict", and no verdict must never block anyone.

That direction is the whole safety argument. A wrong conviction locks a real
customer out for ten minutes, so an unavailable, confused or malformed model
has to fail open, and each of the cases below is a shape that was actually
observed against a live endpoint.
"""

from __future__ import annotations

import pytest

from parchi import ai_guard
from parchi.ai_guard import CONFIDENCE_GATE, AttackAssessment, _build_evidence, assess_attack

SIGNALS = {"detectors_fired": [{"kind": "agent_swarm"}], "attempts_in_60s": 9}


def _answer(monkeypatch, payload):
    """Make the transport return exactly `payload`, or raise it if it is one."""
    def fake(*args, **kwargs):
        if isinstance(payload, Exception):
            raise payload
        return payload
    monkeypatch.setattr(ai_guard.openai_provider, "chat_json_schema", fake)
    monkeypatch.setattr(ai_guard.openai_provider, "resolve_model", lambda m: m)
    monkeypatch.setattr(ai_guard.openai_provider, "load_dotenv", lambda *a, **k: None)


def test_a_good_answer_is_returned(monkeypatch):
    _answer(monkeypatch, {"attack": True, "confidence": 0.91, "reason": "swarm"})
    got = assess_attack("agt_1", SIGNALS)
    assert isinstance(got, AttackAssessment)
    assert got.attack is True and got.confidence == 0.91


@pytest.mark.parametrize("payload, why", [
    ({"attack": True, "confidence": 7, "reason": "sure"},
     "confidence off a 0-1 scale: GLM-5.2 really returned 7"),
    ({"attack": True, "confidence": -0.5, "reason": "sure"},
     "negative confidence"),
    ({"attack": "yes", "confidence": 0.9, "reason": "sure"},
     "attack as a string, not a bool"),
    ({"attack": 1, "confidence": 0.9, "reason": "sure"},
     "1 is not True here: an int would sail through a truthiness check"),
    ({"attack": True, "confidence": "high", "reason": "sure"},
     "confidence as prose"),
    ({"attack": True, "confidence": 0.9, "reason": "   "},
     "a verdict with no stated reason is not reviewable"),
    ({"attack": True, "confidence": 0.9},
     "no reason at all"),
])
def test_an_uninterpretable_answer_is_no_answer(monkeypatch, payload, why):
    _answer(monkeypatch, payload)
    assert assess_attack("agt_1", SIGNALS) is None, why


@pytest.mark.parametrize("boom", [
    RuntimeError("HTTP 402"),          # the account ran out of credit
    RuntimeError("HTTP 429"),          # rate limited
    TimeoutError("timed out"),
    ValueError("model returned an empty message"),
])
def test_a_model_that_cannot_answer_never_convicts(monkeypatch, boom):
    """The failure of an opinion must not become a punishment."""
    _answer(monkeypatch, boom)
    assert assess_attack("agt_1", SIGNALS) is None


def test_the_confidence_gate_is_the_number_the_server_enforces():
    """The gate lives in one place, because it is the price of a false block."""
    assert 0.0 < CONFIDENCE_GATE <= 1.0
    from demo import server
    assert server.CONFIDENCE_GATE is CONFIDENCE_GATE


def test_a_low_confidence_conviction_is_returned_but_does_not_meet_the_gate(monkeypatch):
    """Below the gate the opinion is recorded and nobody is blocked."""
    _answer(monkeypatch, {"attack": True, "confidence": CONFIDENCE_GATE - 0.1,
                          "reason": "might be a farm"})
    got = assess_attack("agt_1", SIGNALS)
    assert got is not None and got.attack is True
    assert got.confidence < CONFIDENCE_GATE


def test_the_evidence_is_bounded(monkeypatch):
    """An attacker who controls line descriptions controls prompt length."""
    signals = {
        "cart_lines": ["x" * 500] * 400,
        **{f"filler_{i}": i for i in range(80)},
    }
    evidence = _build_evidence("agt_1", signals)
    assert len(evidence.splitlines()) <= 25
    assert max(len(ln) for ln in evidence.splitlines()) < 700


def test_the_reason_is_capped_before_it_reaches_an_alert(monkeypatch):
    _answer(monkeypatch, {"attack": True, "confidence": 0.9, "reason": "z" * 5000})
    got = assess_attack("agt_1", SIGNALS)
    assert got is not None and len(got.reason) <= 300


def test_the_prompt_shows_the_model_well_formed_json(monkeypatch):
    """The evidence is substituted with replace(), so braces are not doubled.

    A doubled brace here would put `{{"attack": ...}}` in front of the model as
    its example output, which is not the JSON it is being asked for.
    """
    assert "{{" not in ai_guard.PROMPT
    assert '{"attack": true|false' in ai_guard.PROMPT
    filled = ai_guard.PROMPT.replace("{evidence}", _build_evidence("a", SIGNALS))
    assert "{evidence}" not in filled


def test_the_prompt_states_what_a_false_conviction_costs():
    """Measured, not stylistic: without this the model convicted 15/18 of the
    benign cases in eval/adjudicator.py. See FAILURES.md entry 16."""
    assert "ten minutes" in ai_guard.PROMPT
    assert "clear the account unless" in ai_guard.PROMPT.lower()
