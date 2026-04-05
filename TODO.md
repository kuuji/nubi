# Nubi — TODO

## Bugs
- [ ] TaskSpec status doesn't persist — PATCH returns 200 but phase stays "Executing"
- [ ] Agent only creates 1 of 3 files in e2e test — ran but didn't complete full task

## Backlog
- [ ] Validator agent — writes test suites, commits to branch, runs them
- [ ] Reviewer agent — read-only evaluation, approve/reject with feedback
- [ ] Loop resolution — retry logic (gate→executor, validator→executor, reviewer→executor) with max_retries
- [ ] PR output — create GitHub PR from task branch on approval
- [ ] Summary call — single LLM call to produce human-readable narrative from CRD status + traces
- [ ] Planner agent — task decomposition for complex specs (opt-in)
- [ ] Notification output — Discord/Telegram notifications on completion
- [ ] Integration test suite — real K8s API calls (namespace creation, Job spawning, status updates)
- [ ] Helm chart — templated deployment for production

## Ideas
- Langfuse integration for tracing and cost tracking
- Cost budget enforcement based on Langfuse data
- Parallel sub-task execution for independent planner outputs
- Human-in-the-loop approval gates (pause reconciliation)
- GitOps integration examples (ArgoCD + TaskSpec)
