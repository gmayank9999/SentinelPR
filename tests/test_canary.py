import json

from sentinel.canary.monitor import SLO, Snapshot, StepReading, evaluate, reading
from sentinel.canary.proxy import WeightedRouter
from sentinel.canary.run import main


def test_router_is_exact():
    router = WeightedRouter(0.25)
    picks = [router.pick() for _ in range(100)]
    assert picks.count("canary") == 25
    router.set_weight(0.0)
    assert all(router.pick() == "stable" for _ in range(50))
    router.set_weight(1.0)
    assert router.pick() == "canary"


def test_step_reading_uses_deltas():
    r = reading(Snapshot(100, 1, 5.0, 3.0), Snapshot(300, 5, 7.0, 3.0))
    assert (r.requests, r.errors, r.error_rate, r.p95_ms) == (200, 4, 0.02, 7.0)


def test_slo_evaluation():
    stable = StepReading(500, 0, 0.0, 10.0)
    assert evaluate(stable, StepReading(100, 0, 0.0, 12.0), SLO()).healthy
    errors = evaluate(stable, StepReading(100, 5, 0.05, 12.0), SLO())
    assert not errors.healthy and any("error rate" in b for b in errors.breaches)
    slow = evaluate(stable, StepReading(100, 0, 0.0, 400.0), SLO())
    assert not slow.healthy and "p95" in slow.breaches[0]
    few = evaluate(stable, StepReading(3, 3, 1.0, 999.0), SLO())
    assert few.healthy and few.inconclusive


def test_rollout_rolls_back_an_erroring_canary(tmp_path):
    out = tmp_path / "canary.json"
    main(["--step-seconds", "2", "--canary-env", "UNIERP_FAULT=errors", "--canary-env", "UNIERP_FAULT_RATE=0.5", "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["outcome"] == "rolled_back"
    assert len(report["steps"]) == 1 and report["steps"][0]["weight"] == 0.1
    assert report["exposed_share"] < 0.2
    assert report["events"][-1]["type"] == "rollback"
