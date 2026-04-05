# Nubi (누비)

An agentic harness for orchestrating AI agent workflows with structured task decomposition, sandboxed execution, and layered validation.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

## Live E2E Testing

Run end-to-end tests that exercise the real GitHub + LLM integration:

```bash
./scripts/e2e.sh up     # Build images and deploy controller to local k3d cluster
./scripts/e2e.sh test   # Run a live task against the configured repo (default: kuuji/nubi-playground)
./scripts/e2e.sh clean  # Remove e2e-created TaskSpecs and namespaces
```

**Guarantees:**
- Creates a unique TaskSpec per run with deterministic naming
- Detects terminal Job completion without depending on condition ordering (handles `SuccessCriteriaMet`, `FailureTarget`, etc.)
- Fails fast on fatal pod states (`ErrImagePull`, `ImagePullBackOff`, etc.)
- Verifies TaskSpec terminal status, remote branch existence, and expected file content

**Cleanup:**
- By default, the script deletes the TaskSpec, task namespace, and remote branch after the run
- Set `E2E_KEEP_RESOURCES=1` to preserve resources for debugging
- Artifacts are captured per-run and reported at completion
