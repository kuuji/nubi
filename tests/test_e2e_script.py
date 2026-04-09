"""Contract tests for the live e2e shell harness."""

from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
TASK_NAME_RE = re.compile(r"Creating TaskSpec ([A-Za-z0-9._-]+)\.\.\.")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip())
    path.chmod(0o755)


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def _parse_task_name(output: str) -> str:
    match = TASK_NAME_RE.search(_strip_ansi(output))
    assert match is not None, output
    return match.group(1)


def _read_log_lines(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    return [line for line in log_path.read_text().splitlines() if line]


def _count_logged_calls(log_lines: list[str], program: str) -> int:
    return sum(1 for line in log_lines if line.startswith(f"{program}\t"))


def _logged_args(line: str) -> list[str]:
    _program, raw_args = line.split("\t", 1)
    return json.loads(raw_args)


def _find_logged_call_index(
    log_lines: list[str], program: str, predicate: Callable[[list[str]], bool]
) -> int:
    for index, line in enumerate(log_lines):
        if not line.startswith(f"{program}\t"):
            continue
        if predicate(_logged_args(line)):
            return index
    pytest.fail(f"Did not find {program} call matching predicate in log: {log_lines}")


def _run_e2e(
    tmp_path: Path, command: str, timeout: int = 15, **env_overrides: str
) -> tuple[subprocess.CompletedProcess[str], list[str], str | None]:
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    log_path = tmp_path / "command-log.txt"
    bin_dir.mkdir()
    state_dir.mkdir()

    stub_body = """#!/usr/bin/env python3
import json
import os
import re
import shutil
import sys
from pathlib import Path


def log_call() -> None:
    log_path = Path(os.environ["FAKE_E2E_LOG"])
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{Path(sys.argv[0]).name}\t{json.dumps(sys.argv[1:])}\\n")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def next_sequence_value(name: str, default: str = "") -> str:
    raw = env(name)
    if not raw:
        return default
    values = raw.split("|")
    state_dir = Path(os.environ["FAKE_E2E_STATE"])
    counter_path = state_dir / f"{name.lower()}-index.txt"
    index = 0
    if counter_path.exists():
        index = int(counter_path.read_text(encoding="utf-8"))
    if index < len(values) - 1:
        counter_path.write_text(str(index + 1), encoding="utf-8")
    return values[min(index, len(values) - 1)]


def rendered_taskspec_path() -> Path:
    return Path(os.environ["FAKE_E2E_STATE"]) / "rendered-taskspec.yaml"


def rendered_taskspec_text() -> str:
    path = rendered_taskspec_path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def rendered_task_name() -> str:
    match = re.search(r"^  name: (?P<name>.+)$", rendered_taskspec_text(), re.MULTILINE)
    if match:
        return match.group("name").strip()
    return env("FAKE_E2E_TASK_NAME", "e2e-placeholder")


def rendered_expected_content() -> str:
    override = env("FAKE_E2E_REMOTE_FILE_CONTENT")
    if override:
        return override
    match = re.search(
        r"^    The file content must be exactly: (?P<content>.+)$",
        rendered_taskspec_text(),
        re.MULTILINE,
    )
    if match:
        return match.group("content").strip()
    return env("FAKE_E2E_REMOTE_FILE_CONTENT", "ok")


def kubectl_main() -> int:
    args = sys.argv[1:]
    joined = " ".join(args)
    state_dir = Path(os.environ["FAKE_E2E_STATE"])

    if "apply" in args and "-f" in args:
        source_arg = args[args.index("-f") + 1]
        destination = state_dir / "rendered-taskspec.yaml"
        if source_arg == "-":
            destination.write_text(sys.stdin.read(), encoding="utf-8")
        else:
            shutil.copy(Path(source_arg), destination)
        print("taskspec.nubi.io/e2e created")
        return 0

    if "apply" in args and "-k" in args:
        # Kustomize apply — just acknowledge it
        print("namespace/nubi-system configured")
        return 0

    if "jsonpath={.items[0].status.conditions[0].type}" in joined:
        print(
            next_sequence_value(
                "FAKE_E2E_JOB_STATUS_SEQUENCE",
                env("FAKE_E2E_JOB_STATUS", "Complete"),
            )
        )
        return 0

    if any(
        pattern in joined
        for pattern in [
            "jsonpath={.items[0].status.conditions[*].type}",
            "jsonpath={.status.conditions[*].type}",
        ]
    ):
        print(
            next_sequence_value(
                "FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE",
                env("FAKE_E2E_JOB_CONDITION_TYPES", env("FAKE_E2E_JOB_STATUS", "Complete")),
            )
        )
        return 0

    if any(
        pattern in joined
        for pattern in [
            "jsonpath={.items[0].status.succeeded}",
            "jsonpath={.status.succeeded}",
        ]
    ):
        print(
            next_sequence_value(
                "FAKE_E2E_JOB_SUCCEEDED_SEQUENCE",
                env("FAKE_E2E_JOB_SUCCEEDED", "0"),
            )
        )
        return 0

    if any(
        pattern in joined
        for pattern in [
            "jsonpath={.items[0].status.failed}",
            "jsonpath={.status.failed}",
        ]
    ):
        print(
            next_sequence_value(
                "FAKE_E2E_JOB_FAILED_SEQUENCE",
                env("FAKE_E2E_JOB_FAILED", "0"),
            )
        )
        return 0

    if any(
        pattern in joined
        for pattern in [
            "jsonpath={.items[0].status.containerStatuses[0].state.waiting.reason}",
            "jsonpath={.status.containerStatuses[0].state.waiting.reason}",
        ]
    ):
        print(
            next_sequence_value(
                "FAKE_E2E_POD_WAITING_REASON_SEQUENCE",
                env("FAKE_E2E_POD_WAITING_REASON", ""),
            )
        )
        return 0

    if "jsonpath={.items[0].status.containerStatuses[0].state.terminated.reason}" in joined:
        print(env("FAKE_E2E_POD_TERMINATED_REASON", ""))
        return 0

    if "jsonpath={.items[0].status.containerStatuses[0].state.terminated.exitCode}" in joined:
        print(env("FAKE_E2E_POD_TERMINATED_EXIT_CODE", ""))
        return 0

    if "jsonpath={.status.phase}" in joined:
        print(env("FAKE_E2E_TASK_PHASE", "Done"))
        return 0

    if "jsonpath={.status.workspace.branch}" in joined:
        print(env("FAKE_E2E_WORKSPACE_BRANCH", f"nubi/{rendered_task_name()}"))
        return 0

    if "jsonpath={.status.workspace.headSHA}" in joined:
        print(env("FAKE_E2E_HEAD_SHA", "abc123"))
        return 0

    if "get taskspec" in joined and "-o yaml" in joined:
        print(env("FAKE_E2E_TASKSPEC_YAML", "status:\\n  phase: Done\\n"))
        return 0

    if args[:2] in (["get", "namespace"], ["get", "ns"]):
        namespace = env("FAKE_E2E_CURRENT_NAMESPACE", f"nubi-{rendered_task_name()}")
        extra = env("FAKE_E2E_EXTRA_NAMESPACES")
        lines = [line for line in [namespace, extra] if line]
        if lines:
            print("\\n".join(lines))
            return 0
        return 1

    if "logs" in args:
        print("executor logs")
        return 0

    if "describe" in args:
        print("described")
        return 0

    # get job <name> — return not-found for reviewer jobs unless overridden
    if len(args) >= 3 and args[0] == "get" and args[1] == "job" and "reviewer" in args[2]:
        reviewer_exists = env("FAKE_E2E_REVIEWER_JOB_EXISTS", "1")
        if reviewer_exists != "1":
            return 1

    print("ok")
    return 0


def gh_main() -> int:
    args = sys.argv[1:]
    joined = " ".join(args)
    if "contents" in joined:
        print(rendered_expected_content())
        return 0
    print(env("FAKE_E2E_GH_STDOUT", "ok"))
    return 0


def git_main() -> int:
    print(env("FAKE_E2E_GIT_STDOUT", "ok"))
    return int(env("FAKE_E2E_GIT_EXIT", "0"))


def sleep_main() -> int:
    return 0


log_call()
program = Path(sys.argv[0]).name
if program == "kubectl":
    raise SystemExit(kubectl_main())
if program == "gh":
    raise SystemExit(gh_main())
if program == "git":
    raise SystemExit(git_main())
if program == "sleep":
    raise SystemExit(sleep_main())
raise SystemExit(0)
"""

    for command_name in ["kubectl", "gh", "git", "sleep", "docker", "k3d"]:
        _write_executable(bin_dir / command_name, stub_body)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
            "FAKE_E2E_LOG": str(log_path),
            "FAKE_E2E_STATE": str(state_dir),
            "FAKE_E2E_JOB_STATUS": "Complete",
            "FAKE_E2E_TASK_PHASE": "Done",
            "FAKE_E2E_HEAD_SHA": "abc123",
            "FAKE_E2E_EXTRA_NAMESPACES": "nubi-unrelated",
        }
    )
    env.update(env_overrides)

    completed = subprocess.run(
        ["bash", "scripts/e2e.sh", command],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    rendered_path = state_dir / "rendered-taskspec.yaml"
    rendered = rendered_path.read_text() if rendered_path.exists() else None
    return completed, _read_log_lines(log_path), rendered


def test_test_command_uses_announced_unique_task_name_in_rendered_taskspec(tmp_path: Path) -> None:
    completed, _log_lines, rendered = _run_e2e(tmp_path, "test")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert rendered is not None

    task_name = _parse_task_name(completed.stdout)

    assert f"name: {task_name}" in rendered
    assert f"nubi/{task_name}" in rendered
    assert rendered.count(task_name) >= 3


def test_test_command_cleans_up_only_current_run_and_remote_branch(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(tmp_path, "test")

    assert completed.returncode == 0, completed.stdout + completed.stderr

    task_name = _parse_task_name(completed.stdout)
    task_namespace = f"nubi-{task_name}"
    task_branch = f"nubi/{task_name}"

    assert any(task_name in line and "delete" in line for line in log_lines)
    assert any(task_namespace in line and "delete" in line for line in log_lines)
    assert any(
        task_branch in line and ("delete" in line or "refs/heads" in line) for line in log_lines
    )
    assert not any("nubi-unrelated" in line and "delete" in line for line in log_lines)


def test_test_command_fails_when_workspace_head_sha_is_missing(tmp_path: Path) -> None:
    completed, _log_lines, _rendered = _run_e2e(
        tmp_path,
        "test",
        FAKE_E2E_HEAD_SHA="",
    )

    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "headSHA" in output or "head sha" in output.lower()


def test_test_command_reports_artifacts_on_terminal_phase_failure(tmp_path: Path) -> None:
    completed, _log_lines, _rendered = _run_e2e(
        tmp_path,
        "test",
        FAKE_E2E_TASK_PHASE="Failed",
    )

    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "expected Done" in output
    assert "Artifacts: /tmp/" in output


def test_test_command_fails_fast_when_job_is_complete_but_phase_is_stuck(tmp_path: Path) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_TASK_PHASE="Executing",
            FAKE_E2E_TASKSPEC_YAML="status:\n  phase: Executing\n",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"scripts/e2e.sh test did not fail fast after Job completion: {exc}")

    assert completed is not None
    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "Monitor Job completed" in output or "Reviewer Job completed" in output
    assert "TaskSpec phase remained Executing" in output
    assert "controller status did not persist" in output
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") < 10


def test_test_command_detects_terminal_success_when_complete_is_not_first_condition(
    tmp_path: Path,
) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_JOB_STATUS_SEQUENCE="SuccessCriteriaMet",
            FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE="SuccessCriteriaMet Complete",
            FAKE_E2E_JOB_SUCCEEDED_SEQUENCE="1",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "scripts/e2e.sh test did not recognize terminal success when Complete was not first: "
            f"{exc}"
        )

    assert completed is not None
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _count_logged_calls(log_lines, "sleep") < 5


def test_test_command_keeps_polling_until_later_terminal_success_conditions(tmp_path: Path) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_JOB_STATUS_SEQUENCE="||SuccessCriteriaMet",
            FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE="||SuccessCriteriaMet Complete",
            FAKE_E2E_JOB_SUCCEEDED_SEQUENCE="0|0|1",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"scripts/e2e.sh test did not keep polling until delayed terminal success: {exc}"
        )

    assert completed is not None
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert _count_logged_calls(log_lines, "sleep") >= 2


def test_test_command_detects_terminal_failure_when_failed_is_not_first_condition(
    tmp_path: Path,
) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_JOB_STATUS_SEQUENCE="FailureTarget",
            FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE="FailureTarget Failed",
            FAKE_E2E_JOB_FAILED_SEQUENCE="1",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            "scripts/e2e.sh test did not recognize terminal failure when Failed was not first: "
            f"{exc}"
        )

    assert completed is not None
    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "failed" in output.lower()
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") < 5


def test_test_command_keeps_polling_until_later_terminal_failure_conditions(tmp_path: Path) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_JOB_STATUS_SEQUENCE="||FailureTarget",
            FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE="||FailureTarget Failed",
            FAKE_E2E_JOB_FAILED_SEQUENCE="0|0|1",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"scripts/e2e.sh test did not keep polling until delayed terminal failure: {exc}"
        )

    assert completed is not None
    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "failed" in output.lower()
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") >= 2


def test_test_command_detects_explicit_failed_condition_path(tmp_path: Path) -> None:
    completed: subprocess.CompletedProcess[str] | None = None
    log_lines: list[str] = []
    try:
        completed, log_lines, _rendered = _run_e2e(
            tmp_path,
            "test",
            timeout=2,
            FAKE_E2E_JOB_STATUS_SEQUENCE="Failed",
            FAKE_E2E_JOB_CONDITION_TYPES_SEQUENCE="Failed",
            FAKE_E2E_JOB_FAILED_SEQUENCE="1",
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"scripts/e2e.sh test did not recognize ordinary Failed path: {exc}")

    assert completed is not None
    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "failed" in output.lower()
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") < 5


def test_test_command_fails_fast_on_fatal_pod_waiting_reason(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(
        tmp_path,
        "test",
        FAKE_E2E_JOB_STATUS_SEQUENCE="|||||Complete",
        FAKE_E2E_POD_WAITING_REASON_SEQUENCE="ImagePullBackOff|ImagePullBackOff|ImagePullBackOff",
    )

    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "ImagePullBackOff" in output
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") < 5


def test_test_command_fails_fast_on_fatal_terminated_container(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(
        tmp_path,
        "test",
        FAKE_E2E_JOB_STATUS_SEQUENCE="|||||Complete",
        FAKE_E2E_POD_TERMINATED_REASON="Error",
        FAKE_E2E_POD_TERMINATED_EXIT_CODE="1",
    )

    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "Error (exit 1)" in output
    assert "Artifacts: /tmp/" in output
    assert _count_logged_calls(log_lines, "sleep") < 5


def test_test_command_fails_when_remote_branch_is_missing(tmp_path: Path) -> None:
    completed, _log_lines, _rendered = _run_e2e(
        tmp_path,
        "test",
        FAKE_E2E_GIT_EXIT="2",
    )

    output = _strip_ansi(completed.stdout + completed.stderr)

    assert completed.returncode != 0
    assert "Remote branch" in output


def test_clean_command_only_targets_e2e_namespaces(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(
        tmp_path,
        "clean",
        FAKE_E2E_CURRENT_NAMESPACE="namespace/nubi-e2e-live-stale",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert any("taskspec" in line and "delete" in line for line in log_lines)
    assert any("namespace/nubi-e2e-live" in line and "delete" in line for line in log_lines)
    assert not any("nubi-unrelated" in line and "delete" in line for line in log_lines)


def test_up_command_triggers_controller_rollout_restart(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(tmp_path, "up")

    assert completed.returncode == 0, completed.stdout + completed.stderr

    deploy_index = _find_logged_call_index(
        log_lines,
        "kubectl",
        lambda args: args[:2] == ["apply", "-k"] and "manifests/" in args,
    )
    restart_index = _find_logged_call_index(
        log_lines,
        "kubectl",
        lambda args: (
            args[:2] == ["rollout", "restart"]
            and any(value == "deployment/nubi-controller" for value in args)
        ),
    )

    assert restart_index > deploy_index


def test_up_command_waits_for_readiness_after_controller_restart(tmp_path: Path) -> None:
    completed, log_lines, _rendered = _run_e2e(tmp_path, "up")

    assert completed.returncode == 0, completed.stdout + completed.stderr

    restart_index = _find_logged_call_index(
        log_lines,
        "kubectl",
        lambda args: (
            args[:2] == ["rollout", "restart"]
            and any(value == "deployment/nubi-controller" for value in args)
        ),
    )
    readiness_index = _find_logged_call_index(
        log_lines,
        "kubectl",
        lambda args: (
            (
                args[:2] == ["rollout", "status"]
                and any(value == "deployment/nubi-controller" for value in args)
            )
            or (
                len(args) > 0
                and args[0] == "wait"
                and any(
                    value == "deployment/nubi-controller" or value == "pod" or value == "pods"
                    for value in args
                )
                and any("nubi-controller" in value for value in args)
            )
        ),
    )

    assert readiness_index > restart_index
