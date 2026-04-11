#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-nubi-dev}"
CONTEXT="${CONTEXT:-k3d-${CLUSTER_NAME}}"
CONTROLLER_IMAGE="${CONTROLLER_IMAGE:-ghcr.io/kuuji/nubi-controller:latest}"
AGENT_IMAGE="${AGENT_IMAGE:-ghcr.io/kuuji/nubi-agent:latest}"
TASKSPEC_NAMESPACE="${TASKSPEC_NAMESPACE:-nubi-system}"
E2E_LABEL="app.kubernetes.io/created-by=nubi-e2e"
E2E_TASK_PREFIX="${E2E_TASK_PREFIX:-e2e-live}"
E2E_REPO="${E2E_REPO:-kuuji/nubi-playground}"
E2E_TIMEOUT_SECONDS="${E2E_TIMEOUT_SECONDS:-600}"
E2E_POLL_SECONDS="${E2E_POLL_SECONDS:-5}"
E2E_POST_JOB_PHASE_TIMEOUT_SECONDS="${E2E_POST_JOB_PHASE_TIMEOUT_SECONDS:-30}"
E2E_KEEP_RESOURCES="${E2E_KEEP_RESOURCES:-0}"

is_fatal_pod_reason() {
    case "$1" in
        ErrImagePull|ImagePullBackOff|CreateContainerConfigError|CreateContainerError|InvalidImageName)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[~]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*" >&2; }

usage() {
    cat <<EOF
Usage: $0 <command>

Commands:
  up       - Build images, import to k3d, start controller in cluster
  down     - Delete cluster
  test     - Run live GitHub + LLM e2e test with verification and cleanup
  clean    - Clean up e2e-created TaskSpecs and task namespaces

Examples:
  $0 up
  $0 test
  $0 clean

Environment:
  .env                  - Optional; loaded when present
  E2E_REPO              - Target GitHub repo (default: kuuji/nubi-playground)
  E2E_TIMEOUT_SECONDS   - Overall wait timeout for job/task completion
  E2E_POLL_SECONDS      - Poll interval while waiting
  E2E_KEEP_RESOURCES=1  - Keep TaskSpec, namespace, and branch for debugging
EOF
}

load_env() {
    if [ -f .env ]; then
        info "Loading environment from .env..."
        # shellcheck disable=SC1091
        set -a
        source .env
        set +a
    fi
}

require_cluster() {
    if ! kubectl cluster-info --context "${CONTEXT}" >/dev/null 2>&1; then
        error "Cluster context ${CONTEXT} is not available. Run '$0 up' first."
        exit 1
    fi
}

run_kubectl_capture() {
    local output_file="$1"
    shift
    if ! kubectl "$@" --context "${CONTEXT}" >"${output_file}" 2>&1; then
        return 1
    fi
}

create_task_name() {
    printf '%s-%s-%04d' "${E2E_TASK_PREFIX}" "$(date +%Y%m%d%H%M%S)" "$((RANDOM % 10000))"
}

record_artifact() {
    local output_file="$1"
    shift
    if ! run_kubectl_capture "${output_file}" "$@"; then
        warn "Failed to capture artifact: kubectl $*"
    fi
}

max_poll_attempts() {
    local timeout="$1"
    local poll_seconds="$2"

    if [ "${poll_seconds}" -le 0 ]; then
        printf '1'
        return 0
    fi

    printf '%s' "$(( (timeout + poll_seconds - 1) / poll_seconds ))"
}

report_artifacts() {
    local work_dir="$1"
    if [ -n "${work_dir}" ] && [ -d "${work_dir}" ]; then
        info "Artifacts: ${work_dir}"
    fi
}

wait_for_namespace() {
    local namespace="$1"
    local timeout="$2"
    local attempts=0
    local max_attempts
    max_attempts="$(max_poll_attempts "${timeout}" "${E2E_POLL_SECONDS}")"

    while true; do
        if kubectl get namespace "${namespace}" --context "${CONTEXT}" >/dev/null 2>&1; then
            return 0
        fi

        attempts=$((attempts + 1))
        if [ "${attempts}" -ge "${max_attempts}" ]; then
            return 1
        fi

        sleep "${E2E_POLL_SECONDS}"
    done
}

wait_for_job_terminal_status() {
    local namespace="$1"
    local timeout="$2"
    local fatal_reason
    local status=""
    local attempts=0
    local max_attempts
    max_attempts="$(max_poll_attempts "${timeout}" "${E2E_POLL_SECONDS}")"

    while true; do
        fatal_reason="$(get_executor_pod_failure_reason "${namespace}" || true)"
        if [ -n "${fatal_reason}" ]; then
            printf 'Failed: %s' "${fatal_reason}"
            return 0
        fi

        if status="$(get_executor_job_terminal_status "${namespace}")"; then
            if [ "${status}" = "Complete" ] || [ "${status}" = "Failed" ]; then
                printf '%s' "${status}"
                return 0
            fi
        fi

        attempts=$((attempts + 1))
        if [ "${attempts}" -ge "${max_attempts}" ]; then
            return 1
        fi

        sleep "${E2E_POLL_SECONDS}"
    done
}

wait_for_named_job() {
    local namespace="$1"
    local job_name="$2"
    local timeout="$3"
    local status=""
    local fatal_reason
    local attempts=0
    local max_attempts
    max_attempts="$(max_poll_attempts "${timeout}" "${E2E_POLL_SECONDS}")"

    while true; do
        fatal_reason="$(get_executor_pod_failure_reason "${namespace}" || true)"
        if [ -n "${fatal_reason}" ]; then
            printf 'Failed: %s' "${fatal_reason}"
            return 0
        fi

        if status="$(get_job_terminal_status_by_name "${namespace}" "${job_name}")"; then
            if [ "${status}" = "Complete" ] || [ "${status}" = "Failed" ]; then
                printf '%s' "${status}"
                return 0
            fi
        fi

        attempts=$((attempts + 1))
        if [ "${attempts}" -ge "${max_attempts}" ]; then
            return 1
        fi

        sleep "${E2E_POLL_SECONDS}"
    done
}

get_job_terminal_status_by_name() {
    local namespace="$1"
    local job_name="$2"
    local succeeded
    local failed
    local condition_types

    # Check if job exists
    if ! kubectl get job "${job_name}" -n "${namespace}" --context "${CONTEXT}" >/dev/null 2>&1; then
        return 1
    fi

    succeeded=$(kubectl get job "${job_name}" -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)
    if [[ "${succeeded}" =~ ^[0-9]+$ ]] && [ "${succeeded}" -gt 0 ]; then
        printf 'Complete'
        return 0
    fi

    failed=$(kubectl get job "${job_name}" -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.status.failed}' 2>/dev/null || true)
    if [[ "${failed}" =~ ^[0-9]+$ ]] && [ "${failed}" -gt 0 ]; then
        printf 'Failed'
        return 0
    fi

    condition_types=$(kubectl get job "${job_name}" -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null || true)
    case " ${condition_types} " in
        *" Complete "*)
            printf 'Complete'
            return 0
            ;;
        *" Failed "*)
            printf 'Failed'
            return 0
            ;;
    esac

    return 1
}

# Backward-compat wrapper: checks first job in namespace
get_executor_job_terminal_status() {
    local namespace="$1"
    local job_name
    job_name="$(kubectl get job -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    if [ -z "${job_name}" ]; then
        return 1
    fi
    get_job_terminal_status_by_name "${namespace}" "${job_name}"
}

get_executor_pod_failure_reason() {
    local namespace="$1"
    local waiting_reason
    local terminated_reason
    local terminated_exit_code

    waiting_reason=$(kubectl get pods -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)
    if [ -n "${waiting_reason}" ] && is_fatal_pod_reason "${waiting_reason}"; then
        printf '%s' "${waiting_reason}"
        return 0
    fi

    terminated_reason=$(kubectl get pods -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.reason}' 2>/dev/null || true)
    terminated_exit_code=$(kubectl get pods -n "${namespace}" --context "${CONTEXT}" -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || true)
    if [[ "${terminated_exit_code}" =~ ^[0-9]+$ ]] && [ "${terminated_exit_code}" -ne 0 ] && [ -n "${terminated_reason}" ]; then
        printf '%s (exit %s)' "${terminated_reason}" "${terminated_exit_code}"
        return 0
    fi

    return 1
}

wait_for_terminal_phase() {
    local task_name="$1"
    local timeout="$2"
    local phase=""
    local attempts=0
    local max_attempts
    max_attempts="$(max_poll_attempts "${timeout}" "${E2E_POLL_SECONDS}")"

    while true; do
        phase=$(kubectl get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" --context "${CONTEXT}" -o jsonpath='{.status.phase}' 2>/dev/null || true)
        case "${phase}" in
            Done|Failed|Escalated)
                printf '%s' "${phase}"
                return 0
                ;;
        esac

        attempts=$((attempts + 1))
        if [ "${attempts}" -ge "${max_attempts}" ]; then
            return 1
        fi

        sleep "${E2E_POLL_SECONDS}"
    done
}

get_taskspec_phase() {
    local task_name="$1"
    kubectl get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" --context "${CONTEXT}" -o jsonpath='{.status.phase}' 2>/dev/null || true
}

verify_remote_branch_exists() {
    local repo="$1"
    local branch="$2"
    git ls-remote --exit-code "https://x-access-token:${GITHUB_TOKEN:-}@github.com/${repo}.git" "refs/heads/${branch}" >/dev/null 2>&1
}

fetch_remote_file_content() {
    local repo="$1"
    local branch="$2"
    local path="$3"
    gh api \
        -H "Accept: application/vnd.github.raw" \
        "repos/${repo}/contents/${path}?ref=${branch}"
}

cleanup_run_resources() {
    local task_name="$1"
    local task_namespace="$2"
    local task_branch="$3"
    local repo="$4"

    if [ "${E2E_KEEP_RESOURCES}" = "1" ]; then
        warn "Keeping TaskSpec, namespace, and branch for debugging"
        return
    fi

    info "Cleaning up run resources..."
    kubectl delete taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" --ignore-not-found --context "${CONTEXT}" >/dev/null 2>&1 || true
    kubectl delete namespace "${task_namespace}" --ignore-not-found --context "${CONTEXT}" >/dev/null 2>&1 || true
    git push "https://x-access-token:${GITHUB_TOKEN:-}@github.com/${repo}.git" --delete "${task_branch}" >/dev/null 2>&1 || true
}

cmd_up() {
    if ! k3d cluster list -o json 2>/dev/null | grep -q "\"name\":\"${CLUSTER_NAME}\""; then
        info "Creating k3d cluster ${CLUSTER_NAME}..."
        k3d cluster create "${CLUSTER_NAME}" --no-lb
        kubectl wait --for=condition=Ready nodes --all --timeout=60s --context "${CONTEXT}"
    else
        info "Cluster ${CLUSTER_NAME} already exists"
    fi

    info "Applying CRD..."
    kubectl apply -f manifests/base/crd.yaml --context "${CONTEXT}"

    load_env

    info "Building images..."
    docker build -f images/controller/Dockerfile -t "${CONTROLLER_IMAGE}" .
    docker build -f images/agent/Dockerfile -t "${AGENT_IMAGE}" .

    info "Importing images into k3d..."
    k3d image import "${CONTROLLER_IMAGE}" "${AGENT_IMAGE}" -c "${CLUSTER_NAME}"

    info "Updating credentials secret..."
    _GITHUB_TOKEN="${GITHUB_TOKEN:-dummy-token}"
    _LLM_API_KEY="${LLM_API_KEY:-dummy-key}"
    kubectl create secret generic nubi-credentials \
        --namespace="${TASKSPEC_NAMESPACE}" \
        --from-literal=github-token="${_GITHUB_TOKEN}" \
        --from-literal=llm-api-key="${_LLM_API_KEY}" \
        --dry-run=client -o yaml | kubectl apply -f - --context "${CONTEXT}"

    info "Deploying controller..."
    kubectl apply -k manifests/ --context "${CONTEXT}"

    info "Restarting controller rollout..."
    kubectl rollout restart deployment/nubi-controller -n "${TASKSPEC_NAMESPACE}" --context "${CONTEXT}"

    info "Waiting for controller rollout to be ready..."
    kubectl rollout status deployment/nubi-controller -n "${TASKSPEC_NAMESPACE}" --timeout=120s --context "${CONTEXT}"

    kubectl config use-context "${CONTEXT}"

    info ""
    info "Setup complete!"
    info "  Cluster: ${CLUSTER_NAME}"
    info "  Controller: running in cluster"
    info "  Images: imported to k3d"
    info ""
    info "Run '$0 test' to execute a live e2e test"
}

cmd_down() {
    info "Deleting cluster ${CLUSTER_NAME}..."
    k3d cluster delete "${CLUSTER_NAME}" 2>/dev/null || true
    info "Done"
}

cmd_clean() {
    info "Cleaning up e2e artifacts..."
    kubectl delete taskspec -n "${TASKSPEC_NAMESPACE}" -l "${E2E_LABEL}" --ignore-not-found --context "${CONTEXT}" >/dev/null 2>&1 || true

    while IFS= read -r namespace_name; do
        [ -n "${namespace_name}" ] || continue
        case "${namespace_name}" in
            namespace/nubi-${E2E_TASK_PREFIX}-*)
                kubectl delete "${namespace_name}" --ignore-not-found --context "${CONTEXT}" >/dev/null 2>&1 || true
                ;;
        esac
    done < <(kubectl get namespace -o name --context "${CONTEXT}" 2>/dev/null || true)

    info "Done"
}

cmd_test() {
    load_env
    require_cluster

    local task_name
    local task_namespace
    local task_branch
    local executor_job_name
    local reviewer_job_name
    local monitor_job_name
    local target_file
    local expected_content
    local work_dir
    local task_file
    local terminal_phase
    local job_status
    local reviewer_status
    local monitor_status
    local workspace_branch
    local head_sha
    local remote_content
    local review_json
    local monitor_json

    task_name="$(create_task_name)"
    task_namespace="nubi-${task_name}"
    task_branch="nubi/${task_name}"
    executor_job_name="nubi-executor-${task_name}"
    reviewer_job_name="nubi-reviewer-${task_name}"
    monitor_job_name="nubi-monitor-${task_name}"
    target_file="TEST-${task_name}.txt"
    expected_content="Hello from Nubi live e2e ${task_name}"

    work_dir="$(mktemp -d "/tmp/${task_name}-XXXXXX")"
    task_file="${work_dir}/taskspec.yaml"

    fail_run() {
        local message="$1"
        record_artifact "${work_dir}/taskspec-status.yaml" get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" -o yaml
        record_artifact "${work_dir}/executor.log" logs "job/${executor_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/executor-job.txt" describe job "${executor_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/reviewer.log" logs "job/${reviewer_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/reviewer-job.txt" describe job "${reviewer_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/monitor.log" logs "job/${monitor_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/monitor-job.txt" describe job "${monitor_job_name}" -n "${task_namespace}"
        record_artifact "${work_dir}/all-pods.txt" get pods -n "${task_namespace}" -o wide
        error "${message}"
        exit 1
    }

    trap "report_artifacts '${work_dir}'; cleanup_run_resources '${task_name}' '${task_namespace}' '${task_branch}' '${E2E_REPO}'" EXIT

    info "Creating TaskSpec ${task_name}..."
    cat > "${task_file}" <<EOF
apiVersion: nubi.io/v1
kind: TaskSpec
metadata:
  name: ${task_name}
  namespace: ${TASKSPEC_NAMESPACE}
  labels:
    app.kubernetes.io/created-by: nubi-e2e
spec:
  description: |
    Create exactly one file at the repository root named ${target_file}.
    The file content must be exactly: ${expected_content}
    The expected task branch is ${task_branch}.
    Do not modify any other files.
  type: code-change
  inputs:
    repo: "${E2E_REPO}"
    branch: main
  constraints:
    timeout: 300s
    total_timeout: 420s
    resources:
      cpu: "500m"
      memory: "256Mi"
    network_access:
      - "github.com"
    tools:
      - shell
      - git
      - file_read
      - file_write
  validation:
    deterministic: []
    agentic: []
  review:
    enabled: true
  output:
    format: branch
EOF

    kubectl apply -f "${task_file}" --context "${CONTEXT}"

    # --- Stage 1: Wait for executor job ---
    info "Waiting for namespace ${task_namespace}..."
    if ! wait_for_namespace "${task_namespace}" "${E2E_TIMEOUT_SECONDS}"; then
        fail_run "Timed out waiting for namespace ${task_namespace}"
    fi

    info "Waiting for executor job ${executor_job_name}..."
    if ! job_status="$(wait_for_named_job "${task_namespace}" "${executor_job_name}" "${E2E_TIMEOUT_SECONDS}")"; then
        fail_run "Timed out waiting for executor job to reach a terminal state"
    fi

    if [ "${job_status}" != "Complete" ]; then
        fail_run "Executor job finished with status ${job_status}"
    fi
    info "Executor job completed"

    # --- Stage 2: Wait for reviewer job ---
    info "Waiting for reviewer job ${reviewer_job_name}..."
    if ! reviewer_status="$(wait_for_named_job "${task_namespace}" "${reviewer_job_name}" "${E2E_TIMEOUT_SECONDS}")"; then
        fail_run "Timed out waiting for reviewer job to appear or reach a terminal state"
    fi

    if [ "${reviewer_status}" != "Complete" ]; then
        fail_run "Reviewer job finished with status ${reviewer_status}"
    fi
    info "Reviewer job completed"

    # --- Stage 3: Wait for monitor job ---
    info "Waiting for monitor job ${monitor_job_name}..."
    if ! monitor_status="$(wait_for_named_job "${task_namespace}" "${monitor_job_name}" "${E2E_TIMEOUT_SECONDS}")"; then
        fail_run "Timed out waiting for monitor job to appear or reach a terminal state"
    fi

    # Monitor job failure is graceful — task still goes to Done
    if [ "${monitor_status}" != "Complete" ]; then
        warn "Monitor job finished with status ${monitor_status} (graceful degradation)"
    else
        info "Monitor job completed"
    fi

    # --- Stage 4: Wait for terminal phase ---
    if ! terminal_phase="$(wait_for_terminal_phase "${task_name}" "${E2E_POST_JOB_PHASE_TIMEOUT_SECONDS}")"; then
        terminal_phase="$(get_taskspec_phase "${task_name}")"
        fail_run "Monitor Job completed, but TaskSpec phase remained ${terminal_phase:-<empty>} after ${E2E_POST_JOB_PHASE_TIMEOUT_SECONDS}s; controller status did not persist"
    fi

    # --- Collect artifacts ---
    record_artifact "${work_dir}/taskspec-status.yaml" get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" -o yaml
    record_artifact "${work_dir}/executor.log" logs "job/${executor_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/executor-job.txt" describe job "${executor_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/reviewer.log" logs "job/${reviewer_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/reviewer-job.txt" describe job "${reviewer_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/monitor.log" logs "job/${monitor_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/monitor-job.txt" describe job "${monitor_job_name}" -n "${task_namespace}"
    record_artifact "${work_dir}/all-pods.txt" get pods -n "${task_namespace}" -o wide

    # --- Verify phase ---
    if [ "${terminal_phase}" != "Done" ]; then
        fail_run "TaskSpec finished in phase ${terminal_phase}; expected Done"
    fi

    # --- Verify TaskSpec status fields ---
    workspace_branch="$(kubectl get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" --context "${CONTEXT}" -o jsonpath='{.status.workspace.branch}' 2>/dev/null || true)"
    if [ "${workspace_branch}" != "${task_branch}" ]; then
        fail_run "Expected status.workspace.branch=${task_branch}, got '${workspace_branch}'"
    fi

    head_sha="$(kubectl get taskspec "${task_name}" -n "${TASKSPEC_NAMESPACE}" --context "${CONTEXT}" -o jsonpath='{.status.workspace.headSHA}' 2>/dev/null || true)"
    if [ -z "${head_sha}" ]; then
        fail_run "Expected non-empty status.workspace.headSHA"
    fi

    # --- Verify remote branch and files ---
    if ! verify_remote_branch_exists "${E2E_REPO}" "${task_branch}"; then
        fail_run "Remote branch ${task_branch} was not found on ${E2E_REPO}"
    fi

    remote_content="$(fetch_remote_file_content "${E2E_REPO}" "${task_branch}" "${target_file}" 2>/dev/null || true)"
    if [ "${remote_content}" != "${expected_content}" ]; then
        fail_run "Remote file ${target_file} did not match expected content"
    fi

    # --- Verify review.json exists on branch ---
    review_json="$(fetch_remote_file_content "${E2E_REPO}" "${task_branch}" ".nubi/${task_name}/review.json" 2>/dev/null || true)"
    if [ -z "${review_json}" ]; then
        fail_run "Expected .nubi/${task_name}/review.json on branch ${task_branch}, but file not found"
    fi
    info "review.json found on branch"

    # --- Verify monitor.json exists on branch ---
    monitor_json="$(fetch_remote_file_content "${E2E_REPO}" "${task_branch}" ".nubi/${task_name}/monitor.json" 2>/dev/null || true)"
    if [ -n "${monitor_json}" ]; then
        info "monitor.json found on branch"
    else
        warn "monitor.json not found on branch (monitor may have failed gracefully)"
    fi

    info "Live e2e test passed"
    info "  TaskSpec: ${task_name}"
    info "  Namespace: ${task_namespace}"
    info "  Branch: ${task_branch}"
    info "  Review: $(echo "${review_json}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("decision","?"))' 2>/dev/null || echo 'parse error')"
    if [ -n "${monitor_json}" ]; then
        info "  Monitor: $(echo "${monitor_json}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("decision","?"))' 2>/dev/null || echo 'parse error')"
    fi
}

case "${1:-}" in
    up)   cmd_up ;;
    down) cmd_down ;;
    test) cmd_test ;;
    clean) cmd_clean ;;
    help|--help|-h) usage ;;
    *)
        usage
        exit 1
        ;;
esac
