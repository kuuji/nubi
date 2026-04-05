"""Tests for nubi.crd.schema — GatePolicy, GateThreshold CRD model additions."""

from __future__ import annotations

from nubi.crd.schema import (
    GateCategory,
    GatePolicy,
    GateThreshold,
)


class TestGateThreshold:
    def test_defaults(self) -> None:
        threshold = GateThreshold()
        assert threshold.max_cc == 10
        assert threshold.max_cognitive == 15
        assert threshold.diff_lines_max == 500

    def test_custom_values(self) -> None:
        threshold = GateThreshold(max_cc=5, max_cognitive=8, diff_lines_max=100)
        assert threshold.max_cc == 5
        assert threshold.max_cognitive == 8
        assert threshold.diff_lines_max == 100

    def test_round_trip_json(self) -> None:
        threshold = GateThreshold(max_cc=7, max_cognitive=12, diff_lines_max=200)
        data = threshold.model_dump()
        threshold2 = GateThreshold.model_validate(data)
        assert threshold2 == threshold


class TestGatePolicy:
    def test_empty_allow_block(self) -> None:
        policy = GatePolicy()
        assert policy.allow == []
        assert policy.block == []
        assert policy.thresholds == GateThreshold()

    def test_allow_specific_categories(self) -> None:
        policy = GatePolicy(allow=[GateCategory.LINT, GateCategory.TEST])
        assert policy.allow == [GateCategory.LINT, GateCategory.TEST]
        assert policy.block == []

    def test_block_specific_categories(self) -> None:
        policy = GatePolicy(block=[GateCategory.SECRET_SCAN])
        assert policy.block == [GateCategory.SECRET_SCAN]

    def test_allow_and_block_together(self) -> None:
        policy = GatePolicy(
            allow=[GateCategory.LINT, GateCategory.TEST],
            block=[GateCategory.SECRET_SCAN],
        )
        assert len(policy.allow) == 2
        assert GateCategory.SECRET_SCAN in policy.block

    def test_custom_thresholds(self) -> None:
        thresholds = GateThreshold(max_cc=5, diff_lines_max=100)
        policy = GatePolicy(thresholds=thresholds)
        assert policy.thresholds.max_cc == 5
        assert policy.thresholds.diff_lines_max == 100

    def test_gate_timeout_default(self) -> None:
        policy = GatePolicy()
        assert policy.gate_timeout == 300

    def test_gate_timeout_custom(self) -> None:
        policy = GatePolicy(gate_timeout=600)
        assert policy.gate_timeout == 600

    def test_full_policy(self) -> None:
        policy = GatePolicy(
            allow=[GateCategory.LINT, GateCategory.TEST, GateCategory.COMPLEXITY],
            block=[GateCategory.SECRET_SCAN],
            thresholds=GateThreshold(max_cc=8, max_cognitive=10, diff_lines_max=200),
            gate_timeout=450,
        )
        assert len(policy.allow) == 3
        assert GateCategory.COMPLEXITY in policy.allow
        assert policy.thresholds.max_cc == 8
        assert policy.gate_timeout == 450

    def test_round_trip_json(self) -> None:
        policy = GatePolicy(
            allow=[GateCategory.LINT],
            block=[GateCategory.SECRET_SCAN],
            thresholds=GateThreshold(max_cc=6),
            gate_timeout=500,
        )
        data = policy.model_dump()
        policy2 = GatePolicy.model_validate(data)
        assert policy2 == policy


class TestGatePolicyValidation:
    def test_empty_allow_allows_all(self) -> None:
        policy = GatePolicy()
        assert policy.allow == []

    def test_block_secret_scan(self) -> None:
        policy = GatePolicy(block=[GateCategory.SECRET_SCAN])
        assert GateCategory.SECRET_SCAN in policy.block

    def test_allow_takes_precedence(self) -> None:
        policy = GatePolicy(
            allow=[GateCategory.LINT],
            block=[GateCategory.LINT],
        )
        assert GateCategory.LINT in policy.allow
        assert GateCategory.LINT in policy.block


class TestGateCategoryInSchema:
    def test_gate_category_from_schema(self) -> None:
        assert GateCategory.COMPLEXITY == "complexity"
        assert GateCategory.LINT == "lint"
        assert GateCategory.TEST == "test"
        assert GateCategory.SECRET_SCAN == "secret_scan"
        assert GateCategory.DIFF_SIZE == "diff_size"

    def test_gate_category_is_string_enum(self) -> None:
        for cat in GateCategory:
            assert isinstance(cat, str)
