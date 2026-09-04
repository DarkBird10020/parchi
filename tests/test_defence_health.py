"""The defence lamp has to mean "answering", not "configured".

The console says the lamp is read from the server so it can never lie. That
was only true of the gate. The lamp also read the call budget, and a budget
counts calls a process is ALLOWED to make, not calls that worked: an expired
key or a spent subscription is refused on every attempt and still spends one
budget slot each time. So a dead endpoint drove the lamp green, at 0 successful
calls, while every adjudication returned nothing and the detectors carried on
alerting as though the AI were reviewing them.

Nothing here reaches the network. The health tracker is a counter, and the
question these tests ask is whether the lamp reads it.
"""

from __future__ import annotations

import pytest

from parchi import openai_provider

demo_server = pytest.importorskip("demo.server")


@pytest.fixture(autouse=True)
def _clean_health():
    """Health is process-global, like the budget it sits beside."""
    openai_provider.reset_health()
    openai_provider.reset_budget()
    gate = demo_server.ai_gate_enabled
    demo_server.ai_gate_enabled = True
    yield
    demo_server.ai_gate_enabled = gate
    openai_provider.reset_health()
    openai_provider.reset_budget()


def _fail(times: int) -> None:
    for _ in range(times):
        openai_provider.health().record_failure("RuntimeError: HTTP 401")


# ------------------------------------------------------------------- the lamp

def test_a_healthy_endpoint_is_green():
    openai_provider.health().record_ok()
    assert demo_server.defence_status()["state"] == "green"


def test_an_endpoint_refusing_every_call_is_not_green():
    """The bug, stated as a test: 0 successes and the lamp said working."""
    _fail(openai_provider.UNHEALTHY_AFTER)
    status = demo_server.defence_status()
    assert status["state"] == "failing"
    assert status["ok_calls"] == 0
    assert status["consecutive_failures"] >= openai_provider.UNHEALTHY_AFTER


def test_one_bad_call_is_weather_not_an_outage():
    """A single timeout must not put an outage on the operator's screen."""
    _fail(1)
    assert demo_server.defence_status()["state"] == "green"


def test_the_lamp_names_the_cause_so_an_operator_can_act():
    """"Not answering" is not actionable. "HTTP 401" is."""
    _fail(openai_provider.UNHEALTHY_AFTER)
    assert "401" in (demo_server.defence_status()["last_error"] or "")


def test_one_success_clears_the_streak():
    """A recovered key must turn the lamp back without a restart."""
    _fail(openai_provider.UNHEALTHY_AFTER + 3)
    assert demo_server.defence_status()["state"] == "failing"
    openai_provider.health().record_ok()
    assert demo_server.defence_status()["state"] == "green"
    assert demo_server.defence_status()["consecutive_failures"] == 0


# --------------------------------------------------------------- which wins

def test_failing_beats_amber_because_it_is_the_actionable_fact():
    """A spent subscription is both busy and broken. Report broken.

    Budget usage climbs on refused calls too, so an exhausted key reaches the
    amber threshold on its way to answering nothing. "Token usage very high"
    tells an operator to buy credit; "not answering" tells them the truth.
    """
    budget = openai_provider.reset_budget(limit=10)
    for _ in range(9):
        budget.spend()
    _fail(openai_provider.UNHEALTHY_AFTER)
    assert demo_server.defence_status()["state"] == "failing"


def test_the_gate_being_off_still_beats_everything():
    """Red is a switch an operator threw. It is not an outage to report."""
    demo_server.ai_gate_enabled = False
    _fail(openai_provider.UNHEALTHY_AFTER)
    assert demo_server.defence_status()["state"] == "red"


def test_a_busy_but_answering_endpoint_is_still_amber():
    budget = openai_provider.reset_budget(limit=10)
    for _ in range(9):
        budget.spend()
    openai_provider.health().record_ok()
    assert demo_server.defence_status()["state"] == "amber"


# ------------------------------------------------------------ the console UI

def test_the_console_renders_every_state_the_server_can_send():
    """A state the page has no word for renders as "undefined" on the band."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "demo" / "console.html").read_text(encoding="utf-8")
    for state in ("green", "red", "amber", "failing"):
        assert f"{state}:" in html or f".dl.{state}" in html, (
            f"console.html has no rendering for defence state {state!r}")
    assert ".dl.failing" in html, "failing has no colour of its own"
