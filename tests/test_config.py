from sentinel.config import Config


def test_defaults_load(tmp_path):
    cfg = Config.load(tmp_path, environ={})
    assert cfg.get("retrieval.top_k") == 10
    assert cfg.get("missing.key", "fallback") == "fallback"
    assert cfg.project_prefix == "demo/uni-erp/"


def test_env_overrides_are_typed(tmp_path):
    cfg = Config.load(
        tmp_path,
        environ={"SENTINEL__RETRIEVAL__TOP_K": "5", "SENTINEL__LLM__ENABLED": "false", "OTHER": "x"},
    )
    assert cfg.get("retrieval.top_k") == 5
    assert cfg.get("llm.enabled") is False


def test_repo_file_and_explicit_overrides(tmp_path):
    (tmp_path / "sentinel.yaml").write_text("project:\n  root: .\nrisk:\n  thresholds:\n    block: 0.9\n")
    cfg = Config.load(tmp_path, environ={}, overrides={"retrieval": {"mode": "dense"}})
    assert cfg.project_prefix == ""
    assert cfg.get("risk.thresholds.block") == 0.9
    assert cfg.get("risk.thresholds.canary") == 0.35  # untouched sibling survives the merge
    assert cfg.get("retrieval.mode") == "dense"
    assert cfg.with_overrides({"retrieval": {"top_k": 3}}).get("retrieval.top_k") == 3
