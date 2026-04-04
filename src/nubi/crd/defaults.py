"""Default constants for TaskSpec CRD fields."""

DEFAULT_TIMEOUT = "300s"
DEFAULT_TOTAL_TIMEOUT = "1800s"
DEFAULT_MAX_RETRIES = 2
DEFAULT_ON_MAX_RETRIES = "escalate"
DEFAULT_OUTPUT_FORMAT = "pr"
DEFAULT_PR_TITLE_PREFIX = "nubi:"
DEFAULT_PR_DRAFT = True
DEFAULT_DECOMPOSITION_ALLOW = False
DEFAULT_DECOMPOSITION_MAX_DEPTH = 2
DEFAULT_DECOMPOSITION_MAX_SUBTASKS = 5
DEFAULT_REVIEW_ENABLED = True
DEFAULT_MONITORING_SUMMARY = True
DEFAULT_RESOURCE_CPU = "1"
DEFAULT_RESOURCE_MEMORY = "512Mi"
DEFAULT_BRANCH = "main"

# -- Namespace lifecycle -----------------------------------------------------

DEFAULT_NAMESPACE_PREFIX = "nubi-"
DEFAULT_GVISOR_RUNTIME_CLASS = "gvisor"
DEFAULT_CLEANUP_TTL_SUCCESS = 3600
DEFAULT_CLEANUP_TTL_FAILURE = 0
LABEL_TASK_ID = "nubi.io/task-id"
LABEL_TASK_TYPE = "nubi.io/task-type"
LABEL_STAGE = "nubi.io/stage"
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"

# -- Credential scoping ------------------------------------------------------

MASTER_SECRET_NAME = "nubi-credentials"
MASTER_SECRET_NAMESPACE = "nubi-system"
CREDENTIAL_GITHUB_TOKEN = "github-token"
CREDENTIAL_LLM_API_KEY = "llm-api-key"

# -- Sandbox job builder -----------------------------------------------------

DEFAULT_AGENT_IMAGE = "ghcr.io/kuuji/nubi-agent:latest"
