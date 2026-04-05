#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="nubi-dev"
CONTEXT="k3d-${CLUSTER_NAME}"
CONTROLLER_IMAGE="ghcr.io/kuuji/nubi-controller:latest"
AGENT_IMAGE="ghcr.io/kuuji/nubi-agent:latest"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[~]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*"; }

usage() {
    cat <<EOF
Usage: $0 <command>

Commands:
  up       - Build images, import to k3d, start controller in cluster
  down     - Delete cluster
  test     - Run e2e test (creates sample TaskSpec, watches results)
  clean    - Clean up test artifacts

Examples:
  $0 up     # Set up everything for e2e testing
  $0 test   # Run an e2e test
  $0 clean  # Remove test TaskSpecs and namespaces

Environment:
  .env      - Should contain GITHUB_TOKEN, LLM_API_KEY for real testing
EOF
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
    kubectl apply -f manifests/crd.yaml --context "${CONTEXT}"

    if [ -f .env ]; then
        info "Loading credentials from .env..."
        # shellcheck disable=SC1091
        set -a; source .env; set +a
    fi

    info "Building images..."
    docker build -f images/controller/Dockerfile -t "${CONTROLLER_IMAGE}" .
    docker build -f images/agent/Dockerfile -t "${AGENT_IMAGE}" .

    info "Importing images into k3d..."
    k3d image import "${CONTROLLER_IMAGE}" "${AGENT_IMAGE}" -c "${CLUSTER_NAME}"

    info "Updating credentials secret..."
    _GITHUB_TOKEN="${GITHUB_TOKEN:-dummy-token}"
    _LLM_API_KEY="${LLM_API_KEY:-dummy-key}"
    kubectl create secret generic nubi-credentials \
        --namespace=nubi-system \
        --from-literal=github-token="${_GITHUB_TOKEN}" \
        --from-literal=llm-api-key="${_LLM_API_KEY}" \
        --dry-run=client -o yaml | kubectl apply -f - --context "${CONTEXT}"

    info "Deploying controller..."
    kubectl apply -f manifests/deployment.yaml --context "${CONTEXT}"

    info "Waiting for controller to be ready..."
    kubectl wait --for=condition=Ready pod -n nubi-system -l app.kubernetes.io/name=nubi-controller --timeout=120s --context "${CONTEXT}"

    kubectl config use-context "${CONTEXT}"

    info ""
    info "Setup complete!"
    info "  Cluster: ${CLUSTER_NAME}"
    info "  Controller: running in cluster"
    info "  Images: imported to k3d"
    info ""
    info "Run '$0 test' to execute an e2e test"
}

cmd_down() {
    info "Deleting cluster ${CLUSTER_NAME}..."
    k3d cluster delete "${CLUSTER_NAME}" 2>/dev/null || true
    info "Done"
}

cmd_clean() {
    info "Cleaning up test artifacts..."
    kubectl delete taskspec -n nubi-system -l app.kubernetes.io/created-by=nubi-e2e --ignore-not-found 2>/dev/null || true
    kubectl get namespaces -o name 2>/dev/null | grep "^namespace/nubi-" | xargs -r kubectl delete 2>/dev/null || true
    info "Done"
}

cmd_test() {
    TASK_NAME="e2e-test-$(date +%s)"
    TASK_FILE="/tmp/${TASK_NAME}.yaml"

    info "Creating TaskSpec ${TASK_NAME}..."
    cat > "${TASK_FILE}" <<'TASKEOF'
apiVersion: nubi.io/v1
kind: TaskSpec
metadata:
  name: e2e-test
  namespace: nubi-system
  labels:
    app.kubernetes.io/created-by: nubi-e2e
spec:
  description: |
    Create a simple README.md file in the kuuji/nubi-playground repository.
    Just create a file called TEST.txt with "Hello from e2e test" as content.
  type: code-change
  inputs:
    repo: "kuuji/nubi-playground"
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
    enabled: false
  output:
    format: branch
TASKEOF

    kubectl apply -f "${TASK_FILE}" --context "${CONTEXT}"

    info "TaskSpec created, watching executor..."
    info "Press Ctrl+C to stop watching"

    TASK_NS="nubi-e2e-test"
    kubectl get pods -n "${TASK_NS}" -w --context "${CONTEXT}" &
    WATCH_PID=$!

    cleanup() {
        kill "${WATCH_PID}" 2>/dev/null || true
        rm -f "${TASK_FILE}"
    }
    trap cleanup EXIT

    for i in {1..120}; do
        sleep 5
        JOB_STATUS=$(kubectl get job -n "${TASK_NS}" --context "${CONTEXT}" -o jsonpath='{.items[0].status.conditions[0].type}' 2>/dev/null || echo "Unknown")
        echo "  [$(date +%H:%M:%S)] Job status: ${JOB_STATUS}"
        
        if [ "${JOB_STATUS}" = "Complete" ]; then
            info "Job completed!"
            break
        elif [ "${JOB_STATUS}" = "Failed" ]; then
            error "Job failed!"
            break
        fi
    done

    echo ""
    info "Executor logs:"
    kubectl logs job/nubi-executor-e2e-test -n "${TASK_NS}" --context "${CONTEXT}" 2>/dev/null || echo "  (no logs available)"
    
    echo ""
    info "TaskSpec status:"
    kubectl get taskspec e2e-test -n nubi-system --context "${CONTEXT}" -o yaml | grep -A10 "^status:" || echo "  (no status)"
}

case "${1:-}" in
    up)   cmd_up ;;
    down) cmd_down ;;
    test) cmd_test ;;
    clean) cmd_clean ;;
    help|--help|-h) usage ;;
    *)     usage ;;
esac
