"""Tests for nubi.crd.defaults — default constants."""

from nubi.crd import defaults
from nubi.crd.schema import TaskSpecSpec

MINIMAL_SPEC: dict = {
    "description": "test task",
    "type": "code-change",
    "inputs": {"repo": "kuuji/test"},
}


# -- 1. Each constant has expected value --------------------------------------


class TestDefaultConstants:
    def test_timeout(self):
        assert defaults.DEFAULT_TIMEOUT == "300s"

    def test_total_timeout(self):
        assert defaults.DEFAULT_TOTAL_TIMEOUT == "1800s"

    def test_max_retries(self):
        assert defaults.DEFAULT_MAX_RETRIES == 2

    def test_on_max_retries(self):
        assert defaults.DEFAULT_ON_MAX_RETRIES == "escalate"

    def test_output_format(self):
        assert defaults.DEFAULT_OUTPUT_FORMAT == "pr"

    def test_pr_title_prefix(self):
        assert defaults.DEFAULT_PR_TITLE_PREFIX == "nubi:"

    def test_pr_draft(self):
        assert defaults.DEFAULT_PR_DRAFT is True

    def test_decomposition_allow(self):
        assert defaults.DEFAULT_DECOMPOSITION_ALLOW is False

    def test_review_enabled(self):
        assert defaults.DEFAULT_REVIEW_ENABLED is True

    def test_resource_cpu(self):
        assert defaults.DEFAULT_RESOURCE_CPU == "1"

    def test_resource_memory(self):
        assert defaults.DEFAULT_RESOURCE_MEMORY == "512Mi"

    def test_branch(self):
        assert defaults.DEFAULT_BRANCH == "main"


# -- 2. Schema models use values from defaults.py ----------------------------


class TestDefaultsAppliedToModels:
    def test_timeout_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.constraints.timeout == defaults.DEFAULT_TIMEOUT

    def test_max_retries_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.loop_policy.max_retries == defaults.DEFAULT_MAX_RETRIES

    def test_output_format_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.output.format == defaults.DEFAULT_OUTPUT_FORMAT

    def test_review_enabled_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.review.enabled == defaults.DEFAULT_REVIEW_ENABLED

    def test_resource_cpu_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.constraints.resources.cpu == defaults.DEFAULT_RESOURCE_CPU

    def test_branch_matches_constant(self):
        spec = TaskSpecSpec.model_validate(MINIMAL_SPEC)
        assert spec.inputs.branch == defaults.DEFAULT_BRANCH
