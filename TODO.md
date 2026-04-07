# Nubi — TODO

## Bugs
- [x] TaskSpec status doesn't persist — PATCH returns 200 but phase stays "Executing" (Fixed: controller annotation patch now uses correct Kubernetes client signature)
- [x] Live e2e test hangs indefinitely — fixed Job terminal detection to handle non-standard condition ordering (SuccessCriteriaMet, FailureTarget)
- [x] Remove unnecessary git `.gitconfig` creation — moved safe.directory to container env vars (GIT_CONFIG_COUNT/KEY/VALUE), removed code-level workarounds

## Backlog
- [ ] Validator agent — writes test suites, commits to branch, runs them
- [x] Reviewer agent — read-only evaluation, approve/reject with feedback, reviewer→executor retry loop
- [ ] Loop resolution — remaining retry paths (validator→executor) with max_retries
- [ ] PR output — create GitHub PR from task branch on approval
- [ ] Summary call — single LLM call to produce human-readable narrative from CRD status + traces
- [ ] Planner agent — task decomposition for complex specs (opt-in)
- [ ] Notification output — Discord/Telegram notifications on completion
- [ ] Controller integration tests — real K8s (k3d), mock LLM (fake agent entrypoint), mock GitHub API. Test all state machine loops: executor→reviewer→Done, request-changes→retry→approve, gates fail→retry→escalate, reject→escalate, timeouts
- [ ] Helm chart — templated deployment for production
- [x] Audit overall project complexity — identify unnecessary moving parts, simplification opportunities, and places where agent workflow can be reduced

## Simplification Plan
- [ ] Phase 1 — lock v1 scope to executor + deterministic gates only
- [ ] Phase 1 — shrink TaskSpec/status surface to implemented features; defer validator/reviewer/planner/monitoring/output fields until they exist
- [ ] Phase 1 — make `src/nubi/crd/schema.py` the single source of truth and generate or verify `manifests/crd.yaml` from it
- [ ] Phase 2 — simplify executor completion flow: put TaskSpec namespace/name on Jobs and update status through one clear completion path
- [ ] Phase 2 — make the host loop own git/gates/push; simplify the executor prompt so the model focuses on editing work, not orchestration
- [ ] Phase 2 — unify runtime config into one source of truth instead of splitting behavior across TaskSpec, controller env, entrypoint env, and prompt defaults
- [ ] Phase 3 — remove unnecessary git/tooling complexity: audit `safe.directory`, drop `.gitconfig` creation if unneeded, and reduce overlapping tool wrappers
- [ ] Phase 3 — trim premature generality: keep one LLM provider path for v1 and defer extra stage/credential abstractions until needed
- [ ] Phase 4 — add integration/e2e coverage around the simplified executor happy path before expanding features again

## Ideas
- Langfuse integration for tracing and cost tracking
- Cost budget enforcement based on Langfuse data
- Parallel sub-task execution for independent planner outputs
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
