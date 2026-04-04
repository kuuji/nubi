# Nubi — History

<!-- Updated by the Planner after each approved task. Format: -->
<!-- ## YYYY-MM-DD — Short description -->
<!-- - What changed and why -->
<!-- - Files affected -->
<!-- - Any decisions made during implementation -->

## 2026-04-03 — Project foundation (scaffold + CRD schema + handler skeleton)

- Created pyproject.toml with kopf, pydantic v2, kubernetes-asyncio deps and dev tooling (pytest, ruff, mypy)
- Implemented TaskSpec CRD Pydantic schema in `src/nubi/crd/schema.py` — 6 StrEnums, 11 frozen spec models, 7 mutable status models with camelCase alias support
- Default constants in `src/nubi/crd/defaults.py` referenced by schema Field() declarations
- kopf handler skeleton in `src/nubi/controller/handlers.py` — on_taskspec_created validates spec and sets phase, on_job_status_change reads labels and logs
- Exception hierarchy in `src/nubi/exceptions.py`
- 45 tests across 3 test files, all passing with ruff + mypy clean
- Decisions: kubernetes-asyncio over kr8s (kopf compatibility), duration strings kept as str (no timedelta parsing in v0.1)
