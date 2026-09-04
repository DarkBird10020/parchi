"""Every scenario button on the landing page, clicked the way a visitor does.

The existing scenario tests each reset the cooldown first, so each one proves
its scenario works *in isolation*. Nobody uses the page in isolation: they
click down the list. Done that way, an early scenario earned a ten-minute
account block and the next nine returned the same generic cooldown sentence
instead of their own verdict, so two thirds of the demo looked broken - on the
page a reviewer opens first, and on camera.

Two separate faults were behind it, and only one was cosmetic.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("PARCHI_AI_GATE", "0")

demo_server = pytest.importorskip("demo.server")
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(demo_server.app)


def _reset() -> None:
    demo_server.cooldowns.reset()
    demo_server.probes.reset()
    demo_server.payee_probes.reset()


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def _scenario_ids() -> list[str]:
    return [s["id"] for s in client.get("/api/scenarios").json()["scenarios"]]


def _run(scenario: str) -> dict:
    r = client.post("/api/authorize", json={"scenario": scenario})
    assert r.status_code == 200, f"{scenario}: HTTP {r.status_code} {r.text[:200]}"
    return r.json()


# --------------------------------------------------------------- in isolation

def test_every_scenario_returns_a_verdict_on_its_own():
    for scenario in _scenario_ids():
        _reset()
        decision = _run(scenario)["decision"]
        assert decision["verdict"] in ("ALLOW", "BLOCK", "STEP_UP")
        assert decision["reason"], f"{scenario} returned an empty reason"


# ------------------------------------------------------- clicked back to back

def test_clicking_down_the_list_does_not_mask_the_scenarios_below():
    """The bug, as a visitor met it.

    One block is legitimate and expected: agent impersonation is a privilege
    escalation that earns the ten-minute hold on its first attempt, so every
    scenario after it in the list is correctly refused. That is the feature.
    What is not acceptable is reaching it before the halfway point, which is
    what a shared probe counter caused.
    """
    ids = _scenario_ids()
    masked = []
    for scenario in ids:
        decision = _run(scenario)["decision"]
        if "cooldown" in decision["reason"].lower():
            masked.append(scenario)

    first_masked = ids.index(masked[0]) if masked else len(ids)
    assert first_masked >= 10, (
        "the demo stops showing distinct verdicts at scenario "
        f"{first_masked + 1} of {len(ids)}: {masked}")


def test_a_single_payee_substitution_never_cools_the_account():
    """The fault worth keeping a test for.

    `ProbeDetector` counted every refusal, whatever its reason, and the payee
    escalation read that shared count. So four unrelated refusals - an expired
    mandate, a wrong method, anything - followed by ONE payee substitution
    tripped a cooldown documented as "repeated payee substitution attempts",
    and wrote that sentence into an operator's alert and the ledger when there
    had been exactly one.
    """
    for scenario in ("over_cap", "expired", "wrong_method",
                     "wrong_category", "replay"):
        _run(scenario)
    assert not demo_server.cooldowns.check("usr_demo").active

    _run("payee_substitution")
    held = demo_server.cooldowns.check("usr_demo")
    assert not held.active, (
        "one payee substitution cooled the account, because unrelated "
        f"refusals were counted toward it: {held.reason!r}")


def test_repeated_payee_substitution_still_earns_the_block():
    """Fixing the false trigger must not remove the real one."""
    for _ in range(demo_server.payee_probes.threshold):
        _run("payee_substitution")
    held = demo_server.cooldowns.check("usr_demo")
    assert held.active, "repeated payee substitution no longer cools the account"
    assert "payee" in held.reason.lower()


def test_the_alert_counts_only_what_it_claims_to_count():
    """The alert said "5 refused payee substitution attempts" after one."""
    for scenario in ("over_cap", "expired", "wrong_method", "wrong_category"):
        _run(scenario)
    for _ in range(demo_server.payee_probes.threshold):
        _run("payee_substitution")

    alerts = [a for a in demo_server.read_alert_records()
              if a.get("kind") == "payee_substitution_blocked"]
    assert alerts, "the payee substitution block no longer raises its alert"
    detail = alerts[-1].get("detail", "")
    stated = int(detail.split()[0])
    assert stated == demo_server.payee_probes.threshold, (
        f"the alert claims {stated} payee substitutions; only "
        f"{demo_server.payee_probes.threshold} were attempted")


# ------------------------------------------------------------------- the page

def test_the_page_explains_a_cooldown_block_rather_than_looking_broken():
    """A repeated generic sentence with no explanation reads as a bug."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "demo" / "index.html").read_text(encoding="utf-8")
    assert 'id="cooldownNote"' in html, (
        "nothing on the page distinguishes 'blocked by the cooldown' from "
        "'failed its own checks'")
    assert "never reached its own checks" in html


def test_the_stage_title_is_not_assigned_twice():
    """A second assignment silently dropped the "initiated by" half."""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "demo" / "index.html").read_text(encoding="utf-8")
    assert html.count('$("#stageTitle").textContent =') == 1, (
        "#stageTitle is assigned more than once; the later write wins and "
        "discards whatever the earlier one built")


# ------------------------------------------------- one click, one answer

def _page() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1]
            / "demo" / "index.html").read_text(encoding="utf-8")


def test_a_superseded_scenario_response_is_dropped_rather_than_rendered():
    """The panel used to show one scenario's checks under another's button.

    Scenarios do not cost the same. A cart refused by a rule answers in under a
    millisecond; one that reaches the model measured 3-18s on this endpoint. So
    clicking a slow scenario and then a fast one rendered the fast answer and
    then let the slow one land on top of it. Every row was real and none of
    them belonged together, which reads exactly like "all the checkpoints are
    incorrect" - because they were.

    `run` now takes a ticket and drops any response that is no longer the
    newest. Asserted on the source because the race needs two live requests of
    different durations, which is a browser test, not this one.
    """
    html = _page()
    assert "let runSeq = 0;" in html, "the run sequencer is gone"
    assert "const seq = ++runSeq;" in html, "run() no longer takes a ticket"
    assert html.count("if(seq !== runSeq) return;") >= 2, (
        "both the success and the error path have to drop a superseded "
        "response; one guard alone still lets a failed slow call overwrite a "
        "newer verdict")


def test_a_running_scenario_says_so():
    """Twelve seconds behind a stale stamp is indistinguishable from a dead page."""
    html = _page()
    assert '"RUNNING"' in html, (
        "nothing tells a visitor the checkpoint is working; on a model-bound "
        "scenario the page looks frozen for seconds")
