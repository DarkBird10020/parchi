"""The behavioural detectors: burst, coupon farming, discount drift.

Three properties each test protects:
- nothing here can change a verdict, only raise a pattern;
- thresholds fire once per incident, not once per attempt;
- a pattern needs *repetition*, so one attempt alone never raises one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from demo import server
from parchi.behavior import BurstDetector, CouponWatcher, check_patterns
from parchi.mandate import Cart, CartLine, IntentMandate, new_mandate

client = TestClient(server.app)


def setup_function():
    server.engine.provider = "heuristic"
    client.post("/api/reset")


def _cart(code: str = "", claimed: int = 0, agent: str = "agt_demo") -> Cart:
    return Cart((CartLine("running shoes", "footwear", 420_000),), "upi",
                "mrc_bluleaf", agent_id=agent, discount_code=code,
                discount_paise=claimed)


def _mandate() -> IntentMandate:
    return new_mandate("usr_demo", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
                       "buy running shoes")


# -------------------------------------------------------------------------- /
# burst detector
# -------------------------------------------------------------------------- /

def test_a_single_attempt_never_raises_a_burst():
    burst = BurstDetector(threshold=8, window_seconds=60)
    patterns = check_patterns(_cart(), _mandate(), burst, CouponWatcher(), "ALLOW")
    assert not [p for p in patterns if p.kind == "purchase_burst"]


def test_burst_fires_once_at_the_threshold_not_on_every_attempt_after():
    burst = BurstDetector(threshold=8, window_seconds=60)
    fired = 0
    for _ in range(12):
        patterns = check_patterns(_cart(), _mandate(), burst, CouponWatcher(), "ALLOW")
        fired += sum(1 for p in patterns if p.kind == "purchase_burst")
    assert fired == 1


def test_a_burst_counts_allowed_attempts_not_just_refused_ones():
    """The whole point: a bot that wants volume gets it one ALLOW at a time."""
    burst = BurstDetector(threshold=8, window_seconds=60)
    for _ in range(7):
        check_patterns(_cart(), _mandate(), burst, CouponWatcher(), "ALLOW")
    patterns = check_patterns(_cart(), _mandate(), burst, CouponWatcher(), "ALLOW")
    burst_pattern = [p for p in patterns if p.kind == "purchase_burst"]
    assert burst_pattern and burst_pattern[0].severity == "high"


# -------------------------------------------------------------------------- /
# coupon watcher
# -------------------------------------------------------------------------- /

def test_one_coupon_attempt_is_not_farming():
    watcher = CouponWatcher(hot_threshold=5, hot_window_seconds=120,
                            max_mandates_per_code=12)
    assert watcher.observe(_cart("SAVE10", 10_000), _mandate()) == []


def test_repeated_attempts_at_one_code_go_hot():
    watcher = CouponWatcher(hot_threshold=5, hot_window_seconds=120,
                            max_mandates_per_code=99)
    patterns: list = []
    for _ in range(5):
        patterns = watcher.observe(_cart("SAVE10", 42_000), _mandate())
    kinds = [p.kind for p in patterns]
    assert "coupon_hot" in kinds
    # Once per incident, not once per attempt.
    assert watcher.observe(_cart("SAVE10", 42_000), _mandate()) == []


def test_farming_needs_distinct_mandates_not_repeats():
    """Two actors sharing a code is a sale; dozens of mandates on one code is
    harvested slips. Repeats of the SAME mandate never reach the threshold."""
    watcher = CouponWatcher(hot_threshold=99, hot_window_seconds=120,
                            max_mandates_per_code=2)
    m1 = _mandate()
    for _ in range(10):
        assert not [p for p in watcher.observe(_cart("SAVE10", 10_000), m1)
                    if p.kind == "coupon_farming"]
    m2 = new_mandate("usr_other", "mrc_bluleaf", ("upi",), 500_000, ("footwear",),
                     "buy running shoes")
    patterns = watcher.observe(_cart("SAVE10", 10_000), m2)
    farming = [p for p in patterns if p.kind == "coupon_farming"]
    assert farming and farming[0].severity == "critical"


def test_discount_drift_fires_on_two_different_claimed_values():
    watcher = CouponWatcher(hot_threshold=99, hot_window_seconds=120,
                            max_mandates_per_code=99)
    watcher.observe(_cart("SAVE10", 10_000), _mandate())
    watcher.observe(_cart("SAVE10", 90_000), _mandate())
    drift = watcher.observe_claimed_value("SAVE10", 10_000)
    assert drift and drift.kind == "discount_drift" and drift.severity == "high"
    assert "Rs 900.00" in drift.detail and "Rs 100.00" in drift.detail
    # One alert per discovery.
    assert watcher.observe_claimed_value("SAVE10", 10_000) is None


def test_matching_claims_never_drift():
    watcher = CouponWatcher(hot_threshold=99, hot_window_seconds=120,
                            max_mandates_per_code=99)
    for _ in range(4):
        watcher.observe(_cart("SAVE10", 10_000), _mandate())
    assert watcher.observe_claimed_value("SAVE10", 10_000) is None


def test_check_patterns_suppresses_drift_when_a_code_alert_fired():
    burst = BurstDetector(threshold=99, window_seconds=60)
    watcher = CouponWatcher(hot_threshold=3, hot_window_seconds=120,
                            max_mandates_per_code=99)
    watcher.observe(_cart("SAVE10", 10_000), _mandate())
    watcher.observe(_cart("SAVE10", 90_000), _mandate())
    patterns = check_patterns(_cart("SAVE10", 42_000), _mandate(), burst,
                              watcher, "BLOCK")
    kinds = [p.kind for p in patterns]
    assert "coupon_hot" in kinds
    assert "discount_drift" not in kinds  # suppressed: one incident, one alert


# -------------------------------------------------------------------------- /
# the demo scenarios, end to end
# -------------------------------------------------------------------------- /

def test_coupon_drift_scenario_raises_the_drift_alert():
    body = client.post("/api/authorize", json={"scenario": "coupon_drift"}).json()
    assert body["decision"]["verdict"] == "BLOCK"
    alerts = client.get("/api/alerts").json()["alerts"]
    kinds = {a["kind"] for a in alerts}
    assert "discount_drift" in kinds
    drift = next(a for a in alerts if a["kind"] == "discount_drift")
    assert "save10" in drift["detail"]  # codes are normalised on record


def test_coupon_burst_scenario_raises_the_hot_alert():
    client.post("/api/authorize", json={"scenario": "coupon_burst"})
    alerts = client.get("/api/alerts").json()["alerts"]
    assert any(a["kind"] == "coupon_hot" for a in alerts)


def test_burst_scenario_still_allows_and_raises_the_burst_alert():
    """Every verdict correct, the rate is the signal, and the operator hears."""
    body = client.post("/api/authorize", json={"scenario": "burst"}).json()
    assert body["decision"]["verdict"] == "ALLOW"
    alerts = client.get("/api/alerts").json()["alerts"]
    burst_alerts = [a for a in alerts if a["kind"] == "purchase_burst"]
    assert burst_alerts, "an eight-purchase spree raised no burst alert"
    assert burst_alerts[0]["severity"] == "high"


def test_reset_clears_the_detectors_so_a_demo_starts_clean():
    client.post("/api/authorize", json={"scenario": "coupon_burst"})
    first = client.get("/api/alerts").json()["alerts"]
    assert any(a["kind"] == "coupon_hot" for a in first)
    client.post("/api/reset")
    # The watcher forgot the code, so the same burst is a fresh incident.
    client.post("/api/authorize", json={"scenario": "coupon_burst"})
    again = client.get("/api/alerts").json()["alerts"]
    hot = [a for a in again if a["kind"] == "coupon_hot"]
    assert len(hot) == 1


def test_demo_books_verify_a_correct_coupon_and_refuse_a_wrong_one():
    """The demo books must make check_discount behave as documented."""
    from parchi.checks import check_discount
    book = server.COUPONS
    ok = check_discount(_cart("SAVE10", 10_000), book)
    assert ok.passed
    bad = check_discount(_cart("SAVE10", 90_000), book)
    assert not bad.passed and "worth" in bad.reason
