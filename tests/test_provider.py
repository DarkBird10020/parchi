"""Tests for the OpenAI-compatible provider. No network, no key.

The two things worth pinning here are the ones that cost money or leak secrets:
the call budget, and key redaction.
"""

import json
import os

import pytest

from parchi import openai_provider as op
from parchi.intent_match import resolve_provider
from parchi.mandate import Cart, CartLine, new_mandate


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in ("PARCHI_OPENAI_API_KEY", "PARCHI_OPENAI_BASE_URL",
                "PARCHI_MODEL", "PARCHI_MAX_CALLS", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # Run from a directory with no .env, so a developer's real key cannot leak
    # into a test run and change what these assertions mean.
    monkeypatch.chdir(tmp_path)
    op.reset_budget(1200)
    yield


def test_a_missing_key_is_a_clear_error_not_a_silent_default():
    with pytest.raises(op.ProviderNotConfigured):
        op.api_key()


def test_the_key_never_survives_into_a_printable_string(monkeypatch):
    monkeypatch.setenv("PARCHI_OPENAI_API_KEY", "sk-secret-value-123")
    text = op.redact("Bearer sk-secret-value-123 failed at line 4")
    assert "sk-secret-value-123" not in text
    assert "***redacted***" in text


def test_dotenv_never_overrides_a_real_environment_variable(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("PARCHI_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("PARCHI_MODEL", "from-env")
    op.load_dotenv()
    # CI supplies secrets as real env vars; a stale .env must not win.
    assert os.environ["PARCHI_MODEL"] == "from-env"


def test_dotenv_fills_in_what_the_environment_does_not_have(tmp_path):
    (tmp_path / ".env").write_text(
        '# a comment\nPARCHI_MODEL="quoted/model"\n\nnot-a-pair\n', encoding="utf-8")
    op.load_dotenv()
    assert os.environ["PARCHI_MODEL"] == "quoted/model"


def test_the_call_budget_stops_a_runaway_batch():
    op.reset_budget(3)
    for _ in range(3):
        op.budget().spend()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        op.budget().spend()


def test_the_budget_raises_rather_than_degrading_quietly():
    """An exhausted budget must be an error, not a fallback row.

    Degrading would produce a complete scoreboard whose numbers are the
    heuristic's while the table claims a model ran.
    """
    op.reset_budget(0)
    with pytest.raises(RuntimeError):
        op.chat_json("anything", timeout=1.0, model="x")


def test_a_fenced_json_reply_is_still_parsed():
    assert json.loads(op._strip_fence('```json\n{"match": true}\n```')) == {"match": True}
    assert json.loads(op._strip_fence('{"match": false}')) == {"match": False}


def test_provider_resolution_prefers_anthropic_then_openai_then_offline(monkeypatch):
    assert resolve_provider("auto") == "heuristic"
    monkeypatch.setenv("PARCHI_OPENAI_API_KEY", "sk-x")
    assert resolve_provider("auto") == "openai"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert resolve_provider("auto") == "api"
    # An explicit choice is never overridden by what happens to be in the env.
    assert resolve_provider("off") == "off"
    assert resolve_provider("heuristic") == "heuristic"


def test_an_unreachable_endpoint_degrades_instead_of_crashing(monkeypatch):
    """The degraded path is the product, so it has to survive a dead endpoint."""
    from parchi.intent_match import intent_matches

    monkeypatch.setenv("PARCHI_OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("PARCHI_OPENAI_BASE_URL", "https://127.0.0.1:9/v1")
    m = new_mandate(payer_id="u", payee_id="p", allowed_methods=("upi",),
                    max_amount_paise=500_000, allowed_categories=("footwear",),
                    prompt_playback="buy running shoes")
    cart = Cart((CartLine("running shoes", "footwear", 300_000),), "upi", "p")
    v = intent_matches(m, cart, timeout=2.0, provider="openai")
    assert v.degraded is True
    assert v.match is True          # cheap cart: fail open on rules alone
    assert "sk-x" not in v.reason   # and never leak the key into the ledger


def test_the_prompt_does_not_ask_the_model_to_enforce_the_cap():
    """The cap is arithmetic. Handing it to a model made it block valid carts.

    Pinned as a test because the failure was invisible: the model returned a
    confident, fluent, wrong reason and the verdict looked considered.
    """
    from parchi.intent_match import _build_prompt

    m = new_mandate(payer_id="u", payee_id="p", allowed_methods=("upi",),
                    max_amount_paise=500_000, allowed_categories=("footwear",),
                    prompt_playback="buy running shoes")
    cart = Cart((CartLine("running shoes", "footwear", 407_726),), "upi", "p")
    # The prompt is hard-wrapped, so match on collapsed whitespace rather than
    # letting a reflow silently turn this assertion into a no-op.
    prompt = " ".join(_build_prompt(m, cart).split())
    assert "Maximum: Rs" not in prompt
    assert "never answer false because of a price" in prompt
    assert "5,000" not in prompt          # the cap must not reach the model
    assert "4,077.26" in prompt           # the line price still does
