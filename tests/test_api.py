from fastapi.testclient import TestClient

from sentinel.api import create_api
from sentinel.config import Config
from sentinel.data.store import Store


def test_api_runs_and_errors(tmp_path):
    cfg = Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / "w")})
    Store(cfg.workdir / "sentinel.db").save_run({"run_id": "r1", "decision": "CANARY", "risk": 0.4, "pr": {"number": 7}})
    client = TestClient(create_api(cfg))
    assert client.get("/api/health").json()["status"] == "ok"
    runs = client.get("/api/runs").json()
    assert runs[0]["run_id"] == "r1" and runs[0]["pr"]["number"] == 7
    assert client.get("/api/runs/r1").json()["decision"] == "CANARY"
    assert client.get("/api/runs/nope").status_code == 404
    assert client.get("/api/bench/uni-erp").status_code == 404
    assert client.get("/api/canary").json() == []
