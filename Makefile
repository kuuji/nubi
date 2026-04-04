.PHONY: cluster-up cluster-down build dev test test-integration lint smoke clean check-deps

CLUSTER_NAME := nubi-dev
CONTROLLER_IMAGE := ghcr.io/kuuji/nubi-controller:latest
AGENT_IMAGE := ghcr.io/kuuji/nubi-agent:latest

check-deps:
	@command -v k3d >/dev/null 2>&1 || { echo "k3d not found — install from https://k3d.io"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "docker not found"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found"; exit 1; }

cluster-up: check-deps
	@scripts/dev-cluster.sh up

cluster-down:
	@scripts/dev-cluster.sh down

build: check-deps
	docker build -f images/controller/Dockerfile -t $(CONTROLLER_IMAGE) .
	docker build -f images/agent/Dockerfile -t $(AGENT_IMAGE) .
	k3d image import $(CONTROLLER_IMAGE) $(AGENT_IMAGE) -c $(CLUSTER_NAME)

dev:
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; \
	kopf run src/nubi/controller/handlers.py --verbose

test:
	pytest tests/ -v

test-integration:
	@echo "Integration tests not yet implemented"

lint:
	ruff check src/ tests/
	ruff format --check src/ tests/
	mypy src/nubi/

smoke: check-deps
	@scripts/smoke-test.sh

clean:
	@scripts/dev-cluster.sh down || true
	@docker rmi $(CONTROLLER_IMAGE) $(AGENT_IMAGE) 2>/dev/null || true
