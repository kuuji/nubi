#!/usr/bin/env bash
set -euo pipefail

# Integration test cluster setup — idempotent, safe to run multiple times.
# Creates a k3d cluster with CRD, RBAC, dummy secrets, and fake agent image.
# No real LLM keys or GitHub tokens needed.

CLUSTER_NAME="${CLUSTER_NAME:-nubi-integration}"
CONTEXT="k3d-${CLUSTER_NAME}"
FAKE_AGENT_IMAGE="nubi-fake-agent:test"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

GREEN='\033[0;32m'
NC='\033[0m'
info() { echo -e "${GREEN}[+]${NC} $*"; }

# 1. Create cluster if not exists
if ! k3d cluster list -o json 2>/dev/null | grep -q "\"name\":\"${CLUSTER_NAME}\""; then
    info "Creating k3d cluster ${CLUSTER_NAME}..."
    k3d cluster create "${CLUSTER_NAME}" --no-lb
    kubectl wait --for=condition=Ready nodes --all --timeout=60s --context "${CONTEXT}"
else
    info "Cluster ${CLUSTER_NAME} already exists"
fi

# 2. Apply CRD
info "Applying CRD..."
kubectl apply -f "${ROOT_DIR}/manifests/base/crd.yaml" --context "${CONTEXT}"

# 3. Apply namespace + RBAC (not the Deployment — kopf runs in-process during tests)
info "Applying namespace and RBAC..."
kubectl apply -f - --context "${CONTEXT}" <<'EOF'
apiVersion: v1
kind: Namespace
metadata:
  name: nubi-system
  labels:
    app.kubernetes.io/managed-by: nubi
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: nubi-controller
  namespace: nubi-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: nubi-controller
rules:
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["create", "delete", "get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "watch", "delete"]
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["configmaps", "secrets"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: [""]
    resources: ["resourcequotas"]
    verbs: ["create", "get", "list", "watch", "delete"]
  - apiGroups: ["nubi.io"]
    resources: ["taskspecs"]
    verbs: ["get", "list", "watch", "patch"]
  - apiGroups: ["nubi.io"]
    resources: ["taskspecs/status"]
    verbs: ["get", "update", "patch"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch", "update"]
  - apiGroups: ["kopf.dev"]
    resources: ["clusterkopfpeerings"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: nubi-controller
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: nubi-controller
subjects:
  - kind: ServiceAccount
    name: nubi-controller
    namespace: nubi-system
EOF

# 4. Create dummy credentials secret (no real tokens needed — results are mocked)
info "Creating dummy credentials secret..."
kubectl create secret generic nubi-credentials \
    --namespace=nubi-system \
    --from-literal=github-token=fake-integration-token \
    --from-literal=llm-api-key=fake-integration-key \
    --dry-run=client -o yaml | kubectl apply -f - --context "${CONTEXT}"

# 5. Build and import fake agent image
info "Building fake agent image..."
docker build -f "${ROOT_DIR}/images/fake-agent/Dockerfile" -t "${FAKE_AGENT_IMAGE}" "${ROOT_DIR}/images/fake-agent/"

info "Importing fake agent image into k3d..."
k3d image import "${FAKE_AGENT_IMAGE}" -c "${CLUSTER_NAME}"

info ""
info "Integration test cluster ready!"
info "  Cluster: ${CLUSTER_NAME}"
info "  Context: ${CONTEXT}"
info "  Fake agent: ${FAKE_AGENT_IMAGE}"
info ""
info "Run: pytest tests/integration/ -v"
