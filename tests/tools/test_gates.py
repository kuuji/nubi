"""Tests for nubi.tools.gates — discover_gates and run_gates tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from nubi.agents.gate_result import (
    GateCategory,
    GateDiscovery,
    GatePolicy,
    GateResult,
    GateStatus,
)


class TestDiscoverGates:
    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_discovers_python_gates(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="src/foo.py\nsrc/bar.py")
        mock_python.return_value = [
            GateDiscovery(name="ruff", category=GateCategory.LINT, applies_to=["*.py"]),
            GateDiscovery(name="pytest", category=GateCategory.TEST, applies_to=["tests/**/*.py"]),
        ]
        mock_node.return_value = []
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy()
        changed_files = ["src/foo.py", "src/bar.py"]
        result = discover_gates("/workspace", policy, changed_files)

        mock_python.assert_called_once()
        assert any(d.category == GateCategory.LINT for d in result)
        assert any(d.category == GateCategory.TEST for d in result)

    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_discovers_node_gates(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="src/index.js\nsrc/app.js")
        mock_python.return_value = []
        mock_node.return_value = [
            GateDiscovery(name="eslint", category=GateCategory.LINT, applies_to=["*.js"]),
            GateDiscovery(name="jest", category=GateCategory.TEST, applies_to=["tests/**/*.js"]),
        ]
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy()
        result = discover_gates("/workspace", policy, ["src/index.js"])

        mock_node.assert_called_once()
        assert any(d.category == GateCategory.LINT for d in result)

    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_block_respected(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="src/foo.py")
        mock_python.return_value = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        mock_node.return_value = []
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy(block=[GateCategory.LINT])
        result = discover_gates("/workspace", policy, ["src/foo.py"])

        assert not any(d.category == GateCategory.LINT for d in result)
        assert any(d.category == GateCategory.TEST for d in result)

    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_allow_restricts(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="src/foo.py")
        mock_python.return_value = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        mock_node.return_value = []
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy(allow=[GateCategory.TEST])
        result = discover_gates("/workspace", policy, ["src/foo.py"])

        assert not any(d.category == GateCategory.LINT for d in result)
        assert any(d.category == GateCategory.TEST for d in result)

    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_diff_size_always_discovered(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="src/foo.py")
        mock_python.return_value = []
        mock_node.return_value = []
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy()
        result = discover_gates("/workspace", policy, ["src/foo.py"])

        assert any(d.category == GateCategory.DIFF_SIZE for d in result)

    @patch("nubi.tools.verification_parser.parse_verification_commands", return_value=None)
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates._discover_python_gates")
    @patch("nubi.tools.gates._discover_node_gates")
    @patch("nubi.tools.gates._discover_diff_size_gate")
    def test_no_changed_files(
        self,
        mock_diff_size: MagicMock,
        mock_node: MagicMock,
        mock_python: MagicMock,
        mock_subprocess: MagicMock,
        mock_parser: MagicMock,
    ) -> None:
        from nubi.tools.gates import discover_gates

        mock_subprocess.return_value = MagicMock(returncode=0, stdout="")
        mock_python.return_value = []
        mock_node.return_value = []
        mock_diff_size.return_value = GateDiscovery(
            name="diff_size", category=GateCategory.DIFF_SIZE, applies_to=["*"]
        )

        policy = GatePolicy()
        result = discover_gates("/workspace", policy, [])

        assert not any(d.category == GateCategory.LINT for d in result)
        assert not any(d.category == GateCategory.TEST for d in result)


class TestDiscoverPythonGates:
    @patch("nubi.tools.gates.which")
    def test_discovers_ruff_when_present(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_python_gates

        mock_which.side_effect = lambda cmd: "/usr/bin/ruff" if "ruff" in cmd else None

        discoveries = _discover_python_gates(["src/foo.py"], "/workspace")

        assert any(d.name == "ruff" and d.category == GateCategory.LINT for d in discoveries)

    @patch("nubi.tools.gates.which")
    def test_discovers_pytest_when_present(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_python_gates

        mock_which.side_effect = lambda cmd: "/usr/bin/pytest" if "pytest" in cmd else None

        discoveries = _discover_python_gates(["tests/test_foo.py"], "/workspace")

        assert any(d.name == "pytest" and d.category == GateCategory.TEST for d in discoveries)

    @patch("nubi.tools.gates.which")
    def test_discovers_radon_when_present(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_python_gates

        mock_which.side_effect = lambda cmd: "/usr/bin/radon" if "radon" in cmd else None

        discoveries = _discover_python_gates(["src/complex.py"], "/workspace")

        assert any(d.name == "radon" and d.category == GateCategory.COMPLEXITY for d in discoveries)

    @patch("nubi.tools.gates.which")
    def test_no_tools_returns_empty(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_python_gates

        mock_which.return_value = None

        discoveries = _discover_python_gates(["src/foo.py"], "/workspace")

        assert discoveries == []


class TestDiscoverNodeGates:
    @patch("nubi.tools.gates.which")
    def test_discovers_eslint_when_present(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_node_gates

        mock_which.side_effect = lambda cmd: "/usr/bin/eslint" if "eslint" in cmd else None

        discoveries = _discover_node_gates(["src/index.js"], "/workspace")

        assert any(d.name == "eslint" and d.category == GateCategory.LINT for d in discoveries)

    @patch("nubi.tools.gates.which")
    def test_discovers_jest_when_present(self, mock_which: MagicMock) -> None:
        from nubi.tools.gates import _discover_node_gates

        mock_which.side_effect = lambda cmd: "/usr/bin/jest" if "jest" in cmd else None

        discoveries = _discover_node_gates(["src/__tests__/foo.test.js"], "/workspace")

        assert any(d.name == "jest" and d.category == GateCategory.TEST for d in discoveries)


class TestDiscoverDiffSizeGate:
    def test_returns_diff_size_discovery(self) -> None:
        from nubi.tools.gates import _discover_diff_size_gate

        discovery = _discover_diff_size_gate(["src/foo.py"], "/workspace")

        assert discovery.name == "diff_size"
        assert discovery.category == GateCategory.DIFF_SIZE
        assert discovery.applies_to == ["*"]


class TestRunGates:
    @patch("nubi.tools.gates._run_single_gate")
    def test_runs_gates_sequentially(self, mock_run_single: MagicMock) -> None:
        from nubi.tools.gates import run_gates

        mock_run_single.side_effect = [
            GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED),
            GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.PASSED),
        ]

        discovered = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        policy = GatePolicy()
        result = run_gates(discovered, "/workspace", policy)

        assert mock_run_single.call_count == 2
        assert result.overall_passed is True

    @patch("nubi.tools.gates._run_single_gate")
    def test_runs_all_gates_even_on_failure(self, mock_run_single: MagicMock) -> None:
        """All gates run so the agent sees every failure at once."""
        from nubi.tools.gates import run_gates

        mock_run_single.side_effect = [
            GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.FAILED),
            GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.PASSED),
        ]

        discovered = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        policy = GatePolicy()
        result = run_gates(discovered, "/workspace", policy)

        assert mock_run_single.call_count == 2
        assert result.overall_passed is False

    @patch("nubi.tools.gates._run_single_gate")
    def test_all_passed_overall_passed(self, mock_run_single: MagicMock) -> None:
        from nubi.tools.gates import run_gates

        mock_run_single.side_effect = [
            GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED),
            GateResult(name="pytest", category=GateCategory.TEST, status=GateStatus.PASSED),
        ]

        discovered = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        policy = GatePolicy()
        result = run_gates(discovered, "/workspace", policy)

        assert result.overall_passed is True

    @patch("nubi.tools.gates._run_single_gate")
    def test_skipped_counts_as_failure(self, mock_run_single: MagicMock) -> None:
        from nubi.tools.gates import run_gates

        mock_run_single.side_effect = [
            GateResult(name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED),
            GateResult(name="eslint", category=GateCategory.LINT, status=GateStatus.SKIPPED),
        ]

        discovered = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="eslint", category=GateCategory.LINT),
        ]
        policy = GatePolicy()
        result = run_gates(discovered, "/workspace", policy)

        assert result.overall_passed is False

    def test_no_gates_discovered_fails(self) -> None:
        from nubi.tools.gates import run_gates

        policy = GatePolicy()
        result = run_gates([], "/workspace", policy)

        assert result.overall_passed is False

    @patch("nubi.tools.gates._run_single_gate")
    def test_returns_gates_result_with_discovered(self, mock_run_single: MagicMock) -> None:
        from nubi.tools.gates import run_gates

        mock_run_single.return_value = GateResult(
            name="ruff", category=GateCategory.LINT, status=GateStatus.PASSED
        )

        discovered = [
            GateDiscovery(name="ruff", category=GateCategory.LINT),
            GateDiscovery(name="pytest", category=GateCategory.TEST),
        ]
        policy = GatePolicy()
        result = run_gates(discovered, "/workspace", policy)

        assert len(result.discovered) == 2
        assert len(result.gates) == 2


class TestRunSingleGate:
    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_tool_not_found_returns_failed(
        self, mock_which: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = None

        discovery = GateDiscovery(name="nonexistent", category=GateCategory.LINT)
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.FAILED
        assert "not found" in result.output.lower()

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_ruff_lint_success(self, mock_which: MagicMock, mock_subprocess: MagicMock) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/ruff"
        # First call: git diff --name-only; Second call: ruff check
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="foo.py\n", stderr=""),
            MagicMock(returncode=0, stdout="No issues found", stderr=""),
        ]

        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.PASSED
        assert "ruff" in result.name

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_ruff_lint_failure(self, mock_which: MagicMock, mock_subprocess: MagicMock) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/ruff"
        # First call: git diff --name-only; Second call: ruff check
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="foo.py\n", stderr=""),
            MagicMock(returncode=1, stdout="Errors found", stderr=""),
        ]

        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.FAILED

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_pytest_failure(self, mock_which: MagicMock, mock_subprocess: MagicMock) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/pytest"
        mock_subprocess.return_value = MagicMock(returncode=1, stdout="2 failed", stderr="")

        discovery = GateDiscovery(name="pytest", category=GateCategory.TEST)
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.FAILED

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_timeout_expired(self, mock_which: MagicMock, mock_subprocess: MagicMock) -> None:
        import subprocess

        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/ruff"
        # First call: git diff --name-only; Second call: timeout
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="foo.py\n", stderr=""),
            subprocess.TimeoutExpired("ruff", timeout=300),
        ]

        discovery = GateDiscovery(name="ruff", category=GateCategory.LINT)
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.FAILED
        assert "timeout" in result.error.lower()

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_radon_complexity_check(
        self, mock_which: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/radon"
        # First call: git diff --name-only; Second call: radon cc -j
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="foo.py\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"foo.py": [{"name": "foo", "complexity": 5}]}',
                stderr="",
            ),
        ]

        discovery = GateDiscovery(
            name="radon",
            category=GateCategory.COMPLEXITY,
        )
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.PASSED

    @patch("nubi.tools.gates.subprocess.run")
    @patch("nubi.tools.gates.which")
    def test_radon_complexity_exceeds_threshold(
        self, mock_which: MagicMock, mock_subprocess: MagicMock
    ) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_which.return_value = "/usr/bin/radon"
        # First call: git diff --name-only; Second call: radon cc -j
        mock_subprocess.side_effect = [
            MagicMock(returncode=0, stdout="foo.py\n", stderr=""),
            MagicMock(
                returncode=0,
                stdout='{"foo.py": [{"name": "complex_func", "complexity": 15}]}',
                stderr="",
            ),
        ]

        discovery = GateDiscovery(
            name="radon",
            category=GateCategory.COMPLEXITY,
        )
        result = _run_single_gate(discovery, "/workspace", GatePolicy(), timeout=300)

        assert result.status == GateStatus.FAILED

    @patch("nubi.tools.gates.subprocess.run")
    def test_diff_size_uses_base_branch(self, mock_subprocess: MagicMock) -> None:
        from nubi.tools.gates import _run_single_gate

        mock_subprocess.return_value = MagicMock(
            returncode=0, stdout="1 file changed, 5 insertions(+)", stderr=""
        )

        discovery = GateDiscovery(name="diff_size", category=GateCategory.DIFF_SIZE)
        policy = GatePolicy(base_branch="develop")
        result = _run_single_gate(discovery, "/workspace", policy, timeout=300)

        cmd_args = mock_subprocess.call_args[0][0]
        assert "origin/develop..HEAD" in " ".join(cmd_args)
        assert result.command == "git diff --stat origin/develop..HEAD"


class TestGateToolRegistry:
    def test_gates_in_tool_groups(self) -> None:
        from nubi.tools import TOOL_GROUPS

        assert "gate" in TOOL_GROUPS

    def test_gate_group_contains_discover_and_run(self) -> None:
        from nubi.tools import TOOL_GROUPS

        gate_tools = TOOL_GROUPS.get("gate", [])
        tool_names = [t.__name__ for t in gate_tools]
        assert "discover_gates" in tool_names
        assert "run_gates" in tool_names
