#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="nubi-dev"
CONTEXT="k3d-${CLUSTER_NAME}"
TASKSPEC="examples/sample-taskspec.yaml"
TASK_NAME="smoke-test-task"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}PASS${NC} $*"; }
fail() { echo -e "  ${RED}FAIL${NC} $*"; }
info() { echo -e "${GREEN}[+]${NC} $*"; }

PASSED=0
FAILED=0

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        pass "$desc"
        PASSED=$((PASSED + 1))
    else
        fail "$desc"
        FAILED=$((FAILED + 1))
    fi
}

cleanup() {
    info "Cleaning up..."
    kubectl delete -f "${TASKSPEC}" --ignore-not-found --context "${CONTEXT}" 2>/dev/null || true
    # Clean up task namespace if it was created
    kubectl delete namespace "nubi-${TASK_NAME}" --ignore-not-found --context "${CONTEXT}" 2>/dev/null || true
}
trap cleanup EXIT

# Verify cluster is up
if ! kubectl cluster-info --context "${CONTEXT}" >/dev/null 2>&1; then
    echo "Cluster ${CLUSTER_NAME} is not running. Run 'make cluster-up' first."
    exit 1
fi

# Verify controller is running (check for kopf peering)
info "Checking if controller is running..."
echo "  (Make sure 'make dev' is running in another terminal)"
echo ""

info "Applying sample TaskSpec..."
kubectl apply -f "${TASKSPEC}" --context "${CONTEXT}"

info "Waiting for controller to process (10s)..."
sleep 10

echo ""
info "Checking results..."

# Check TaskSpec was accepted
check "TaskSpec exists in API" \
    kubectl get taskspec "${TASK_NAME}" --context "${CONTEXT}"

# Check phase moved from empty/Pending
PHASE=$(kubectl get taskspec "${TASK_NAME}" -o jsonpath='{.status.phase}' --context "${CONTEXT}" 2>/dev/null || echo "")
if [ -n "$PHASE" ] && [ "$PHASE" != "Pending" ]; then
    pass "Phase advanced to: ${PHASE}"
    PASSED=$((PASSED + 1))
else
    fail "Phase did not advance (current: '${PHASE:-empty}')"
    FAILED=$((FAILED + 1))
fi

# Check task namespace was created
check "Task namespace nubi-${TASK_NAME} created" \
    kubectl get namespace "nubi-${TASK_NAME}" --context "${CONTEXT}"

# Check scoped secret was created in task namespace
check "Scoped credentials secret created" \
    kubectl get secret -n "nubi-${TASK_NAME}" --context "${CONTEXT}" -o name

# Check executor job was submitted (via events, since cross-namespace ownerRef may cause GC)
JOB_EVENTS=$(kubectl get events -n "nubi-${TASK_NAME}" --context "${CONTEXT}" \
    --field-selector involvedObject.kind=Job 2>/dev/null | grep -c "nubi-executor" || true)
if [ "$JOB_EVENTS" -gt 0 ]; then
    pass "Executor job created (found ${JOB_EVENTS} job events)"
    PASSED=$((PASSED + 1))
else
    fail "No executor job events found"
    FAILED=$((FAILED + 1))
fi

# Check pod was scheduled (via events)
POD_SCHEDULED=$(kubectl get events -n "nubi-${TASK_NAME}" --context "${CONTEXT}" \
    --field-selector reason=Scheduled 2>/dev/null | grep -c "nubi-executor" || true)
if [ "$POD_SCHEDULED" -gt 0 ]; then
    pass "Executor pod was scheduled"
    PASSED=$((PASSED + 1))
else
    fail "Executor pod was not scheduled"
    FAILED=$((FAILED + 1))
fi

# Informational: show events and status
echo ""
info "Task namespace events:"
kubectl get events -n "nubi-${TASK_NAME}" --context "${CONTEXT}" 2>/dev/null || echo "  No events"

echo ""
info "TaskSpec status:"
kubectl get taskspec "${TASK_NAME}" -o yaml --context "${CONTEXT}" 2>/dev/null | grep -A 20 "^status:" || echo "  No status yet"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Results: ${PASSED} passed, ${FAILED} failed"
if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed!${NC}"
fi
