"""Tests for the 9-category decision system."""

from sentinel.risk.policy import base_decision, decide


def test_pass_low():
    """Very low risk -> PASS_LOW."""
    result = decide(0.05, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "PASS_LOW"
    assert base_decision(result.decision) == "PASS"


def test_pass_medium():
    result = decide(0.25, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "PASS_MEDIUM"


def test_pass_high():
    result = decide(0.42, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "PASS_HIGH"


def test_canary_low():
    result = decide(0.52, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "CANARY_LOW"


def test_canary_medium():
    result = decide(0.63, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "CANARY_MEDIUM"


def test_canary_high():
    result = decide(0.80, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "CANARY_HIGH"


def test_block_low():
    result = decide(0.88, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "BLOCK_LOW"


def test_block_medium():
    result = decide(0.93, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "BLOCK_MEDIUM"


def test_block_high():
    result = decide(0.98, 0, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "BLOCK_HIGH"


def test_verified_failures_always_block_high():
    """Test failures -> BLOCK_HIGH regardless of risk score."""
    result = decide(0.1, 3, {"canary": 0.4982, "block": 0.8672})
    assert result.decision == "BLOCK_HIGH"


def test_base_decision():
    assert base_decision("PASS_LOW") == "PASS"
    assert base_decision("CANARY_MEDIUM") == "CANARY"
    assert base_decision("BLOCK_HIGH") == "BLOCK"
    assert base_decision("PASS") == "PASS"
    assert base_decision("BLOCK") == "BLOCK"


def test_legacy_thresholds_compatibility():
    """Old callers checking base tier work via base_decision()."""
    result = decide(0.1, 0, {"canary": 0.4982, "block": 0.8672})
    assert base_decision(result.decision) == "PASS"
    result2 = decide(0.6, 0, {"canary": 0.4982, "block": 0.8672})
    assert base_decision(result2.decision) == "CANARY"
    result3 = decide(0.9, 0, {"canary": 0.4982, "block": 0.8672})
    assert base_decision(result3.decision) == "BLOCK"
