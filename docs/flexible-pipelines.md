# Flexible Pipelines — Design Exploration

## Problem

The current pipeline is hardcoded: executor → gates → reviewer → monitor. This works well for `code-change` tasks, but Nubi could handle other workflows — incident investigation, infrastructure fixes, research — that need different agent compositions.

Example: an infrastructure incident might need an **investigator** (collects logs, metrics, traces, identifies root cause) followed by a **fix executor** (prepares a remediation), then the usual gates and review. The current pipeline can't express this.

## Core question

Who decides which agents are involved?

---

## Option A: Task type determines the pipeline

The CRD already has a `type` field (`code-change`, `research`, `infra`, `investigation`, `refactor`). The controller maps each type to a predefined stage sequence.

```yaml
# Controller-side mapping (not in the CRD — in the controller code)
code-change:    [executor, gates, reviewer, monitor]
investigation:  [investigator, analyzer, executor, gates, reviewer, monitor]
infra:          [investigator, executor, gates, reviewer, monitor]
research:       [researcher, summarizer]
```

**Pros:**
- Simplest to implement — a dict in the controller
- Fully deterministic — the pipeline for a given type is always the same
- Easy to test — each type has a known stage sequence
- CRD doesn't change — `type` already exists

**Cons:**
- Rigid — you can't customize stages per-task, only per-type
- New workflows require new types and controller changes
- Doesn't handle hybrid tasks well (e.g. "investigate and then fix")

**Implementation complexity:** Low. Map type → stage list in the controller, generalize the handler chain.

---

## Option B: Planner agent decides the pipeline

A planner agent runs as the first stage. It reads the task description and outputs a stage plan — which agents to run, in what order, with what tools.

```yaml
status:
  plan:
    stages:
      - role: investigator
        tools: [shell, file_read, kubectl_logs]
      - role: executor
        tools: [shell, git, file_read, file_write]
      - role: reviewer
        tools: [file_read, git_diff, submit_review]
```

The controller then executes the plan sequentially.

**Pros:**
- Most flexible — handles any workflow without predefined types
- The agent can tailor tool access per stage
- New workflows don't require controller changes

**Cons:**
- An agent deciding its own pipeline is hard to reason about and test
- Failure modes are complex — what if the planner produces a bad plan?
- Harder to enforce security invariants (e.g. "reviewer must always be read-only")
- Adds latency and cost — an LLM call before any real work starts

**Implementation complexity:** High. Needs a plan schema, plan validation, a generic stage executor, and guardrails to prevent unsafe plans.

---

## Option C: MCP/planner interview decides before the CRD exists

During the conversational phase (human ↔ AI assistant via MCP), the planner skill determines the right pipeline template. The TaskSpec is created with the full stage plan already baked in.

```yaml
spec:
  pipeline:
    - role: investigator
      description: "Collect logs and identify root cause"
    - role: executor
      description: "Implement the fix"
    - role: reviewer
      focus: [correctness, blast_radius]
```

The human reviews the plan in conversation before it becomes a CRD. The controller just executes what it's given.

**Pros:**
- Best UX — human validates the plan before execution
- CRD is still deterministic once created — controller doesn't guess
- Flexibility lives in the conversation, not in autonomous agent decisions
- Security invariants can be enforced at CRD validation time

**Cons:**
- Requires the planner MCP skill to be smart enough to compose pipelines
- More complex CRD schema
- Doesn't work for fully autonomous / GitOps-driven task submission (no human in the loop)

**Implementation complexity:** Medium. Needs a `pipeline` field in the CRD, generalized controller handlers, and a planner MCP skill that can compose stage lists.

---

## Hybrid: A + C

Use Option A as the default (type → predefined pipeline) but allow Option C as an override (explicit `spec.pipeline` overrides the type default). This gives you:

- Simple tasks just set `type: code-change` and get the standard pipeline
- Complex tasks get a custom pipeline composed during the MCP interview
- GitOps users can define custom pipelines in YAML without needing the MCP
- Controller validates the pipeline at admission time — unsafe configurations are rejected

This is probably the pragmatic path: start with A (just wire up the type mapping), add C later when the planner skill exists.

---

## What needs to happen first

Regardless of which option we pick, the controller needs to be generalized:

1. **Generic stage executor** — instead of `on_executor_completion`, `on_reviewer_completion`, etc., a single handler that advances to the next stage in the pipeline
2. **Agent registry** — a mapping of role → (image, entrypoint, tools, read-only flag) so the sandbox builder can spawn any agent type
3. **Pipeline state in CRD status** — track which stage we're on, not just which phase

These are prerequisites for any flexible pipeline approach.
