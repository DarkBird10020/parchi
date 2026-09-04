"""What the request body has to contain, on an endpoint of reasoning models.

Moving providers broke two things that had nothing to do with the HTTP shape,
and both were invisible from the response code. A reasoning model spends
`max_tokens` on thinking BEFORE it emits any content, so a ceiling that was
generous for a three-field verdict came back as an empty message rather than a
short one - a 200 OK carrying nothing. And the same models answered a
one-sentence question in 58-83s until asked not to reason, which is a timeout
in a payment path rather than a slow answer.

Neither is caught by a test that mocks the provider, because both live in the
body this repo sends. So these assert on the body itself. Nothing here reaches
the network.
"""

from __future__ import annotations

import json

import pytest

from parchi import ai_guard, openai_provider


@pytest.fixture
def sent_body(monkeypatch):
    """Capture the JSON this repo would POST, without POSTing it."""
    captured: dict = {}

    class _Resp:
        status = 200

        @staticmethod
        def read() -> bytes:
            return json.dumps({"choices": [
                {"message": {"content": '{"ok": true}'}}]}).encode()

    class _Conn:
        def request(self, method, url, body=None, headers=None):
            captured.update(json.loads(body))

        def getresponse(self):
            return _Resp()

    monkeypatch.setattr(openai_provider, "_connection",
                        lambda timeout: (_Conn(), ""))
    monkeypatch.setenv("PARCHI_OPENAI_API_KEY", "test-key-not-real")
    openai_provider.reset_budget(50)
    openai_provider.reset_health()
    return captured


def test_the_request_asks_for_no_reasoning(sent_body):
    """58-83s per call became 6-9s. That is the whole reason it is sent.

    It is a request, not a guarantee - the endpoint still returned reasoning
    text - but the latency difference is the difference between a checkpoint
    and a timeout, and an endpoint that does not know the parameter ignores it.
    """
    openai_provider._request("say ok", 10.0, "glm-5.3-flash:dev",
                             {"type": "json_object"}, 900)
    assert sent_body.get("reasoning_effort") == "none"


def test_the_temperature_is_still_pinned(sent_body):
    """A payment decision does not get to be creative."""
    openai_provider._request("say ok", 10.0, "glm-5.3-flash:dev",
                             {"type": "json_object"}, 900)
    assert sent_body["temperature"] == 0


def test_the_adjudicator_leaves_room_for_an_answer():
    """300 tokens was the bug: reasoning ate it and content came back empty.

    The floor is asserted rather than the exact number, because the number is
    a judgement and the property is not: whatever it is set to, it has to be
    large enough that a model which thinks first can still answer.
    """
    import inspect
    source = inspect.getsource(ai_guard.assess_attack)
    tokens = [int(n) for n in
              __import__("re").findall(r"max_tokens=(\d+)", source)]
    assert tokens, "assess_attack no longer sets max_tokens explicitly"
    assert min(tokens) >= 600, (
        f"max_tokens={min(tokens)} leaves a reasoning model no room to answer; "
        "it returns an empty message, not a short one")


# ------------------------------------------------------------ model choice

def test_the_second_opinion_is_a_different_model():
    """Re-asking the same name covers a dropped socket and nothing else.

    The failure this call actually sees is a model that is down, and on a plan
    rated at 100 requests per five hours a wasted retry is expensive.
    """
    assert ai_guard.FALLBACK_GUARD_MODEL != ai_guard.DEFAULT_GUARD_MODEL


def test_every_pinned_model_is_one_the_endpoint_offers():
    """A pinned name the provider retired turns every row into a fallback.

    Not a network check: this pins the names against the documented dev
    catalogue, so a typo in a model id fails here rather than at 3am.
    """
    catalogue = {
        "deepseek-v4-flash:dev", "deepseek-v4-flash-0731:dev",
        "mimo-v2.5:dev", "kimi-k2.6:dev", "kimi-k2.7-code:dev",
        "minimax-m2.7:dev", "glm-5.3:dev", "glm-5.3-flash:dev",
    }
    for name in openai_provider.PREFERRED_MODELS:
        assert name in catalogue, f"{name} is not in the dev catalogue"
    assert ai_guard.DEFAULT_GUARD_MODEL in catalogue
    assert ai_guard.FALLBACK_GUARD_MODEL in catalogue


def test_the_fastest_measured_model_is_the_one_tried_first():
    """GLM flash measured 6-18s; DeepSeek 37-60s on the same prompt.

    The default is a measurement, not a tier preference. This pins the
    ordering so a future edit has to argue with the number.
    """
    assert openai_provider.PREFERRED_MODELS[0] == "glm-5.3-flash:dev"
    assert ai_guard.DEFAULT_GUARD_MODEL == "glm-5.3-flash:dev"
