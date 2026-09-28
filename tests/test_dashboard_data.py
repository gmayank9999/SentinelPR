import json

from sentinel.config import Config
from sentinel.publish.dashboard_data import collect


def test_collect_writes_index_and_files(tmp_path):
    cfg = Config.load(tmp_path, environ={}, overrides={"workdir": str(tmp_path / ".sentinel")})
    runs = cfg.workdir / "runs"
    runs.mkdir(parents=True)
    report = {"run_id": "run-1", "created_at": "2026-09-28T10:00:00", "decision": "BLOCK", "risk": 0.8,
              "elapsed_s": 9.1, "tokens": 0, "pr": {"number": 4, "title": "Tweak", "author": "dev"}}
    (runs / "run-1.json").write_text(json.dumps(report))
    (runs / "broken.json").write_text("{not json")
    (cfg.workdir / "canary").mkdir()
    (cfg.workdir / "canary" / "slow.json").write_text("{}")
    bench = tmp_path / "bench" / "out" / "uni-erp"
    bench.mkdir(parents=True)
    (bench / "results.json").write_text("{}")

    out = tmp_path / "data"
    summary = collect(cfg, out)
    assert summary == {"runs": 1, "bench": ["uni-erp"], "canary": 1, "qa": False}
    index = json.loads((out / "index.json").read_text())
    assert index["runs"][0]["pr"] == {"number": 4, "title": "Tweak", "author": "dev"}
    assert (out / "runs" / "run-1.json").exists() and (out / "bench" / "uni-erp.json").exists()
    assert index["canary"] == ["slow.json"]
