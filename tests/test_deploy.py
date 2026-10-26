"""Staged canary deployments: refusals, real rollouts, proof of routing and rollback, persistence.

The rollout tests start real application processes and a real router, so they are slower than unit
tests (a few seconds each) and are skipped when the demo repository has not been seeded.
"""

import time
from contextlib import ExitStack
from pathlib import Path

import pytest

from sentinel.canary.deploy import DeploymentManager, tolerance
from sentinel.canary.run import checkout, service
from sentinel.canary.targets import DeployError, TargetConfig, builtin_router, capabilities, probe

ROOT = Path(__file__).resolve().parent.parent
APP_REPO = ROOT / ".sentinel" / "uni-erp-repo"
seeded = pytest.mark.skipif(not (APP_REPO / ".git").exists(), reason="demo repository not seeded (python -m sentinel.data.index --seed-demo)")

LOCAL = TargetConfig("local-processes", "local", "local")


def manager(tmp_path, targets=None):
    return DeploymentManager(None, APP_REPO, targets or {"local-processes": LOCAL}, tmp_path)


def wait(mgr, dep_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = mgr.get(dep_id)
        if state["status"] not in ("queued", "provisioning", "rolling_out", "paused"):
            return state
        time.sleep(0.5)
    raise AssertionError("deployment did not finish")


def kinds(mgr, dep_id):
    return [e["kind"] for e in mgr.audit(dep_id)]


# --- refusals (no processes needed) ---------------------------------------------------------------------------


def test_production_is_never_a_valid_environment():
    with pytest.raises(DeployError) as err:
        TargetConfig("prod", "external", "production", {"router_url": "https://prod.example"}).validate()
    assert err.value.code == "production_refused" and err.value.status == 403


@seeded
def test_a_deployment_needs_explicit_confirmation(tmp_path):
    with pytest.raises(DeployError) as err:
        manager(tmp_path).create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle")
    assert err.value.code == "confirmation_required"


@seeded
def test_unknown_targets_refs_and_identical_commits_are_refused(tmp_path):
    m = manager(tmp_path)
    for kwargs, code in [({"target": "nope", "candidate_ref": "demo/subtle"}, "unknown_target"), ({"target": "local-processes", "candidate_ref": "no/such-branch"}, "unknown_ref"),
                         ({"target": "local-processes", "candidate_ref": "main"}, "same_commit"), ({"target": "local-processes", "candidate_ref": "x; rm -rf /"}, "invalid_ref"),
                         ({"target": "local-processes", "candidate_ref": "../../etc"}, "invalid_ref")]:
        with pytest.raises(DeployError) as err:
            m.create(stable_ref="main", confirm=True, **kwargs)
        assert err.value.code == code, kwargs


def test_a_target_that_cannot_split_traffic_says_so_instead_of_pretending(tmp_path):
    external = TargetConfig("staging", "external", "staging", {"stable_url": "http://a", "candidate_url": "http://b"})  # no router_url
    caps = capabilities(external)
    assert caps["weighted_routing"] is False and "cannot split traffic" in caps["note"]
    with pytest.raises(DeployError) as err:
        manager(tmp_path, {"staging": external}).create(target="staging", stable_ref="main", candidate_ref="demo/subtle", confirm=True)
    assert err.value.code == "no_weighted_routing"


def test_routing_tolerance_tightens_with_more_traffic():
    assert tolerance(0.5, 100) > tolerance(0.5, 10_000) >= 0.05


# --- real rollouts ------------------------------------------------------------------------------------------------


@seeded
def test_an_slo_breach_rolls_back_and_the_rollback_is_proven(tmp_path):
    m = manager(tmp_path)
    dep = m.create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle", step_seconds=4, confirm=True, test_fault={"kind": "errors", "rate": 0.6})
    state = wait(m, dep["id"])
    assert state["status"] == "rolled_back" and state["outcome"] == "rolled_back"
    assert state["test_fault"]["injected_for_validation"] is True  # a deliberate fault is labelled as such
    step = state["step_results"][0]
    assert step["routing_verified"] and not step["healthy"] and step["candidate"]["error_rate"] > 0.01
    assert 0.05 < state["exposed_share"] < 0.2  # only the first 10% step was exposed
    # rollback was verified by observation, not assumed
    assert state["rollback_verified"] is True and state["weight_observed"] == 0.0
    check = next(e for e in m.audit(dep["id"]) if e["kind"] == "rollback_check")
    assert check["data"]["probe"].get(f"stable-{state['stable']['sha'][:7]}") == 40
    assert not any(v for k, v in check["data"]["probe"].items() if k.startswith("candidate-"))


@seeded
def test_a_healthy_candidate_is_promoted_only_after_it_serves_every_request(tmp_path):
    m = manager(tmp_path)
    state = wait(m, m.create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle", step_seconds=3, confirm=True)["id"])
    assert state["status"] == "promoted" and state["promotion_verified"] is True
    assert [r["weight"] for r in state["step_results"]] == [0.1, 0.25, 0.5, 1.0]
    for r in state["step_results"]:  # the share the applications actually served matched the requested weight
        assert r["routing_verified"] and abs(r["candidate_share"] - r["weight"]) <= tolerance(r["weight"], r["candidate"]["requests"] + r["stable"]["requests"])
    assert state["step_results"][-1]["stable"]["requests"] == 0


@seeded
def test_operator_abort_restores_the_baseline_and_is_audited(tmp_path):
    m = manager(tmp_path)
    dep = m.create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle", step_seconds=30, confirm=True)
    for _ in range(120):
        if m.get(dep["id"])["status"] == "rolling_out" and m.get(dep["id"])["measurements"]:
            break
        time.sleep(0.5)
    with pytest.raises(DeployError):
        m.create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle", confirm=True)  # one rollout at a time
    m.control(dep["id"], "pause")
    for _ in range(40):  # the traffic window in flight finishes first; paused means the controller reached its checkpoint
        if m.get(dep["id"])["status"] == "paused":
            break
        time.sleep(0.5)
    frozen = len(m.get(dep["id"])["measurements"])
    time.sleep(4)
    assert m.get(dep["id"])["status"] == "paused" and len(m.get(dep["id"])["measurements"]) == frozen  # nothing advances while paused
    m.control(dep["id"], "resume")
    m.control(dep["id"], "abort")
    state = wait(m, dep["id"])
    assert state["outcome"] == "aborted" and state["rollback_verified"] is True and state["weight_observed"] == 0.0
    audit = kinds(m, dep["id"])
    assert [k for k in audit if k == "operator_action"] == ["operator_action"] * 3 and "rollback_check" in audit
    with pytest.raises(DeployError):
        m.control(dep["id"], "pause")  # finished deployments cannot be controlled


@seeded
def test_state_and_audit_trail_persist_and_interrupted_runs_are_not_left_looking_alive(tmp_path):
    m = manager(tmp_path)
    state = wait(m, m.create(target="local-processes", stable_ref="main", candidate_ref="demo/subtle", step_seconds=3, confirm=True, test_fault={"kind": "errors", "rate": 0.6})["id"])
    again = manager(tmp_path)  # a fresh process reads the same records
    assert again.get(state["id"])["status"] == "rolled_back" and again.audit(state["id"])[0]["kind"] == "created"
    # a deployment that was mid-flight when the server died is marked failed, never "running"
    stale = dict(again.get(state["id"]), id="dep-20200101000000-aaaaaa", status="rolling_out")
    again._save(stale)
    recovered = manager(tmp_path)
    assert recovered.get("dep-20200101000000-aaaaaa")["status"] == "failed" and "Interrupted" in recovered.get("dep-20200101000000-aaaaaa")["reason"]


# --- an environment the operator runs (the external-target protocol) ---------------------------------------------


@seeded
def test_external_target_is_driven_and_measured_through_its_router(tmp_path):
    """Stand up a 'staging' ourselves, then let SentinelPR drive it knowing only URLs and the router's admin API."""
    from sentinel import gitutil

    stable, candidate = gitutil.rev_parse(APP_REPO, "main"), gitutil.rev_parse(APP_REPO, "demo/subtle")
    with ExitStack() as stack:
        scratch = tmp_path / "trees"
        scratch.mkdir()
        s_dir = stack.enter_context(checkout(APP_REPO, stable, ".", scratch, "stable"))
        c_dir = stack.enter_context(checkout(APP_REPO, candidate, ".", scratch, "candidate"))
        s_url = stack.enter_context(service(s_dir, f"stable-{stable[:7]}", {}))
        c_url = stack.enter_context(service(c_dir, f"candidate-{candidate[:7]}", {"UNIERP_FAULT": "errors", "UNIERP_FAULT_RATE": "0.6"}))
        r_url = stack.enter_context(builtin_router(s_url, c_url))
        external = TargetConfig("staging", "external", "staging", {"stable_url": s_url, "candidate_url": c_url, "router_url": r_url,
                                                                     "stable_version": f"stable-{stable[:7]}", "candidate_version": f"candidate-{candidate[:7]}"})
        m = manager(tmp_path / "state", {"staging": external})
        state = wait(m, m.create(target="staging", stable_ref="main", candidate_ref="demo/subtle", step_seconds=4, confirm=True)["id"])
        assert state["status"] == "rolled_back" and state["rollback_verified"] is True
        # the operator's own router really was restored: nothing reaches the candidate any more
        assert probe(r_url, 20).get(f"candidate-{candidate[:7]}", 0) == 0
