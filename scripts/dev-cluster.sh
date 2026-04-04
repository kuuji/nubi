#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="nubi-dev"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[~]${NC} $*"; }
error() { echo -e "${RED}[!]${NC} $*"; }

cmd_up() {
    # Create cluster if it doesn't exist
    if k3d cluster list -o json 2>/dev/null | grep -q "\"name\":\"${CLUSTER_NAME}\""; then
        warn "Cluster ${CLUSTER_NAME} already exists, skipping creation"
    else
        info "Creating k3d cluster ${CLUSTER_NAME}..."
        k3d cluster create "${CLUSTER_NAME}" --no-lb
    fi

    info "Waiting for nodes to be ready..."
    kubectl wait --for=condition=Ready nodes --all --timeout=60s --context "k3d-${CLUSTER_NAME}"

    info "Applying CRD..."
    kubectl apply -f manifests/crd.yaml --context "k3d-${CLUSTER_NAME}"

    info "Applying RBAC from deployment manifest..."
    kubectl apply -f manifests/deployment.yaml --context "k3d-${CLUSTER_NAME}"
    # Remove the Deployment (we run kopf locally) and RuntimeClass (no gVisor in k3d)
    kubectl delete deployment nubi-controller -n nubi-system --ignore-not-found --context "k3d-${CLUSTER_NAME}"
    kubectl delete runtimeclass gvisor --ignore-not-found --context "k3d-${CLUSTER_NAME}"

    # Load .env if it exists for real credentials
    if [ -f .env ]; then
        info "Loading credentials from .env..."
        # shellcheck disable=SC1091
        set -a; source .env; set +a
        _GITHUB_TOKEN="${GITHUB_TOKEN:-dev-dummy-token}"
        _LLM_API_KEY="${LLM_API_KEY:-dev-dummy-key}"
    else
        warn "No .env file found — using dummy credentials (copy .env.example to .env for real testing)"
        _GITHUB_TOKEN="dev-dummy-token"
        _LLM_API_KEY="dev-dummy-key"
    fi

    info "Creating credentials secret..."
    kubectl create secret generic nubi-credentials \
        --namespace=nubi-system \
        --from-literal=github-token="${_GITHUB_TOKEN}" \
        --from-literal=llm-api-key="${_LLM_API_KEY}" \
        --dry-run=client -o yaml | kubectl apply -f - --context "k3d-${CLUSTER_NAME}"

    info "Switching kubectl context to k3d-${CLUSTER_NAME}..."
    kubectl config use-context "k3d-${CLUSTER_NAME}"

    echo ""
    info "Cluster ready!"
    info "  CRD:    taskspecs.nubi.io applied"
    info "  RBAC:   nubi-controller ServiceAccount + ClusterRole"
    info "  Secret: nubi-credentials in nubi-system (dummy values)"
    echo ""
    info "Next steps:"
    info "  make build   — build & import images into cluster"
    info "  make dev     — run controller locally (set NUBI_RUNTIME_CLASS='' for k3d)"
}

cmd_down() {
    info "Deleting k3d cluster ${CLUSTER_NAME}..."
    k3d cluster delete "${CLUSTER_NAME}" 2>/dev/null || true
    info "Cluster deleted."
}

case "${1:-}" in
    up)   cmd_up ;;
    down) cmd_down ;;
    *)
        echo "Usage: $0 [up|down]"
        exit 1
        ;;
esac
