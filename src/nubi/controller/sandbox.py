"""gVisor Job builder — create sandboxed executor Jobs."""

from __future__ import annotations

import logging
import os
import re

from kubernetes_asyncio.client import (
    BatchV1Api,
    V1Capabilities,
    V1Container,
    V1EmptyDirVolumeSource,
    V1EnvVar,
    V1EnvVarSource,
    V1Job,
    V1JobSpec,
    V1ObjectMeta,
    V1PodSpec,
    V1PodTemplateSpec,
    V1ResourceRequirements,
    V1SeccompProfile,
    V1SecretKeySelector,
    V1SecurityContext,
    V1Volume,
    V1VolumeMount,
)
from kubernetes_asyncio.client.exceptions import ApiException

from nubi.crd.defaults import (
    CREDENTIAL_GITHUB_TOKEN,
    CREDENTIAL_LLM_API_KEY,
    DEFAULT_AGENT_IMAGE,
    DEFAULT_GVISOR_RUNTIME_CLASS,
    LABEL_MANAGED_BY,
    LABEL_STAGE,
    LABEL_TASK_ID,
    LABEL_TASKSPEC_NAMESPACE,
)
from nubi.crd.schema import TaskSpecSpec
from nubi.exceptions import SandboxError

logger = logging.getLogger(__name__)

_DURATION_RE = re.compile(r"^(\d+)s$")


def parse_duration(duration: str) -> int:
    """Convert a duration string like '300s' to integer seconds.

    Only supports seconds suffix for v0.1.
    """
    match = _DURATION_RE.match(duration)
    if not match:
        raise ValueError(f"Invalid duration format: {duration!r} (expected '<int>s')")
    return int(match.group(1))


def build_executor_job(
    task_name: str,
    ns_name: str,
    spec: TaskSpecSpec,
    secret_name: str,
    taskspec_namespace: str,
) -> V1Job:
    """Construct a gVisor-sandboxed executor Job."""
    job_name = f"nubi-executor-{task_name}"[:63]
    timeout = parse_duration(spec.constraints.timeout)

    env_from_secret = [
        V1EnvVar(
            name="GITHUB_TOKEN",
            value_from=V1EnvVarSource(
                secret_key_ref=V1SecretKeySelector(name=secret_name, key=CREDENTIAL_GITHUB_TOKEN),
            ),
        ),
        V1EnvVar(
            name="LLM_API_KEY",
            value_from=V1EnvVarSource(
                secret_key_ref=V1SecretKeySelector(name=secret_name, key=CREDENTIAL_LLM_API_KEY),
            ),
        ),
    ]

    env_plain = [
        V1EnvVar(name="NUBI_TASK_ID", value=task_name),
        V1EnvVar(name="NUBI_REPO", value=spec.inputs.repo),
        V1EnvVar(name="NUBI_BRANCH", value=spec.inputs.branch),
        V1EnvVar(name="NUBI_DESCRIPTION", value=spec.description),
        V1EnvVar(name="NUBI_TOOLS", value=",".join(spec.constraints.tools)),
        V1EnvVar(name="NUBI_LLM_PROVIDER", value=os.environ.get("NUBI_LLM_PROVIDER", "anthropic")),
        # uid 65534 (nobody) has no home dir — point HOME to workspace for git config etc.
        V1EnvVar(name="HOME", value="/workspace"),
        # emptyDir mount is root-owned; git 2.35+ refuses to operate unless safe.directory is set.
        # Setting via env avoids writing a .gitconfig file into the workspace.
        V1EnvVar(name="GIT_CONFIG_COUNT", value="1"),
        V1EnvVar(name="GIT_CONFIG_KEY_0", value="safe.directory"),
        V1EnvVar(name="GIT_CONFIG_VALUE_0", value="/workspace"),
    ]

    # Optional LLM config — pass through from controller env to agent pod
    model_id = os.environ.get("NUBI_MODEL_ID")
    if model_id:
        env_plain.append(V1EnvVar(name="NUBI_MODEL_ID", value=model_id))
    base_url = os.environ.get("NUBI_LLM_BASE_URL")
    if base_url:
        env_plain.append(V1EnvVar(name="NUBI_LLM_BASE_URL", value=base_url))

    agent_image = os.environ.get("NUBI_AGENT_IMAGE", DEFAULT_AGENT_IMAGE)
    pull_policy = os.environ.get("NUBI_AGENT_IMAGE_PULL_POLICY") or None

    container = V1Container(
        name="executor",
        image=agent_image,
        image_pull_policy=pull_policy,
        working_dir="/workspace",
        resources=V1ResourceRequirements(
            requests={
                "cpu": spec.constraints.resources.cpu,
                "memory": spec.constraints.resources.memory,
            },
            limits={
                "cpu": spec.constraints.resources.cpu,
                "memory": spec.constraints.resources.memory,
            },
        ),
        security_context=V1SecurityContext(
            run_as_non_root=True,
            run_as_user=65534,
            allow_privilege_escalation=False,
            read_only_root_filesystem=False,
            capabilities=V1Capabilities(drop=["ALL"]),
            seccomp_profile=V1SeccompProfile(type="RuntimeDefault"),
        ),
        env=env_from_secret + env_plain,
        volume_mounts=[
            V1VolumeMount(name="workspace", mount_path="/workspace"),
        ],
    )

    rc = os.environ.get("NUBI_RUNTIME_CLASS", DEFAULT_GVISOR_RUNTIME_CLASS)

    return V1Job(
        metadata=V1ObjectMeta(
            name=job_name,
            namespace=ns_name,
            labels={
                LABEL_TASK_ID: task_name,
                LABEL_TASKSPEC_NAMESPACE: taskspec_namespace,
                LABEL_STAGE: "executor",
                LABEL_MANAGED_BY: "nubi",
            },
        ),
        spec=V1JobSpec(
            backoff_limit=0,
            active_deadline_seconds=timeout,
            ttl_seconds_after_finished=600,
            template=V1PodTemplateSpec(
                spec=V1PodSpec(
                    runtime_class_name=rc if rc else None,
                    restart_policy="Never",
                    containers=[container],
                    volumes=[
                        V1Volume(
                            name="workspace",
                            empty_dir=V1EmptyDirVolumeSource(),
                        ),
                    ],
                ),
            ),
        ),
    )


def build_reviewer_job(
    task_name: str,
    ns_name: str,
    spec: TaskSpecSpec,
    secret_name: str,
    taskspec_namespace: str,
) -> V1Job:
    """Construct a gVisor-sandboxed reviewer Job."""
    job_name = f"nubi-reviewer-{task_name}"[:63]
    timeout = parse_duration(spec.constraints.timeout)

    env_from_secret = [
        V1EnvVar(
            name="GITHUB_TOKEN",
            value_from=V1EnvVarSource(
                secret_key_ref=V1SecretKeySelector(name=secret_name, key=CREDENTIAL_GITHUB_TOKEN),
            ),
        ),
        V1EnvVar(
            name="LLM_API_KEY",
            value_from=V1EnvVarSource(
                secret_key_ref=V1SecretKeySelector(name=secret_name, key=CREDENTIAL_LLM_API_KEY),
            ),
        ),
    ]

    review_focus = ",".join(spec.review.focus) if spec.review.focus else ""

    env_plain = [
        V1EnvVar(name="NUBI_TASK_ID", value=task_name),
        V1EnvVar(name="NUBI_REPO", value=spec.inputs.repo),
        V1EnvVar(name="NUBI_BRANCH", value=spec.inputs.branch),
        V1EnvVar(name="NUBI_DESCRIPTION", value=spec.description),
        V1EnvVar(name="NUBI_TOOLS", value="shell,git_read,file_read,file_list,review"),
        V1EnvVar(name="NUBI_REVIEW_FOCUS", value=review_focus),
        V1EnvVar(
            name="NUBI_LLM_PROVIDER",
            value=os.environ.get(
                "NUBI_REVIEWER_LLM_PROVIDER",
                os.environ.get("NUBI_LLM_PROVIDER", "anthropic"),
            ),
        ),
        V1EnvVar(name="HOME", value="/workspace"),
        V1EnvVar(name="GIT_CONFIG_COUNT", value="1"),
        V1EnvVar(name="GIT_CONFIG_KEY_0", value="safe.directory"),
        V1EnvVar(name="GIT_CONFIG_VALUE_0", value="/workspace"),
    ]

    # Reviewer-specific model overrides, falling back to shared config
    model_id = os.environ.get("NUBI_REVIEWER_MODEL_ID", os.environ.get("NUBI_MODEL_ID"))
    if model_id:
        env_plain.append(V1EnvVar(name="NUBI_MODEL_ID", value=model_id))
    base_url = os.environ.get(
        "NUBI_REVIEWER_LLM_BASE_URL", os.environ.get("NUBI_LLM_BASE_URL")
    )
    if base_url:
        env_plain.append(V1EnvVar(name="NUBI_LLM_BASE_URL", value=base_url))

    agent_image = os.environ.get("NUBI_AGENT_IMAGE", DEFAULT_AGENT_IMAGE)
    pull_policy = os.environ.get("NUBI_AGENT_IMAGE_PULL_POLICY") or None

    container = V1Container(
        name="reviewer",
        image=agent_image,
        image_pull_policy=pull_policy,
        command=["python", "-m", "nubi.reviewer_entrypoint"],
        working_dir="/workspace",
        resources=V1ResourceRequirements(
            requests={
                "cpu": spec.constraints.resources.cpu,
                "memory": spec.constraints.resources.memory,
            },
            limits={
                "cpu": spec.constraints.resources.cpu,
                "memory": spec.constraints.resources.memory,
            },
        ),
        security_context=V1SecurityContext(
            run_as_non_root=True,
            run_as_user=65534,
            allow_privilege_escalation=False,
            read_only_root_filesystem=False,
            capabilities=V1Capabilities(drop=["ALL"]),
            seccomp_profile=V1SeccompProfile(type="RuntimeDefault"),
        ),
        env=env_from_secret + env_plain,
        volume_mounts=[
            V1VolumeMount(name="workspace", mount_path="/workspace"),
        ],
    )

    rc = os.environ.get("NUBI_RUNTIME_CLASS", DEFAULT_GVISOR_RUNTIME_CLASS)

    return V1Job(
        metadata=V1ObjectMeta(
            name=job_name,
            namespace=ns_name,
            labels={
                LABEL_TASK_ID: task_name,
                LABEL_TASKSPEC_NAMESPACE: taskspec_namespace,
                LABEL_STAGE: "reviewer",
                LABEL_MANAGED_BY: "nubi",
            },
        ),
        spec=V1JobSpec(
            backoff_limit=0,
            active_deadline_seconds=timeout,
            ttl_seconds_after_finished=600,
            template=V1PodTemplateSpec(
                spec=V1PodSpec(
                    runtime_class_name=rc if rc else None,
                    restart_policy="Never",
                    containers=[container],
                    volumes=[
                        V1Volume(
                            name="workspace",
                            empty_dir=V1EmptyDirVolumeSource(),
                        ),
                    ],
                ),
            ),
        ),
    )


async def create_reviewer_job(
    task_name: str,
    ns_name: str,
    spec: TaskSpecSpec,
    secret_name: str,
    taskspec_namespace: str,
) -> str:
    """Build and create the reviewer Job. Idempotent (409 = no-op).

    Returns the Job name.
    """
    job = build_reviewer_job(task_name, ns_name, spec, secret_name, taskspec_namespace)
    job_name = job.metadata.name
    batch_api = BatchV1Api()

    try:
        await batch_api.create_namespaced_job(namespace=ns_name, body=job)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("Job %s in %s already exists, continuing", job_name, ns_name)
            return job_name
        raise SandboxError(f"Failed to create job {job_name} in {ns_name}: {exc}") from exc

    return job_name


async def create_executor_job(
    task_name: str,
    ns_name: str,
    spec: TaskSpecSpec,
    secret_name: str,
    taskspec_namespace: str,
) -> str:
    """Build and create the executor Job. Idempotent (409 = no-op).

    Returns the Job name.
    """
    job = build_executor_job(task_name, ns_name, spec, secret_name, taskspec_namespace)
    job_name = job.metadata.name
    batch_api = BatchV1Api()

    try:
        await batch_api.create_namespaced_job(namespace=ns_name, body=job)
    except ApiException as exc:
        if exc.status == 409:
            logger.info("Job %s in %s already exists, continuing", job_name, ns_name)
            return job_name
        raise SandboxError(f"Failed to create job {job_name} in {ns_name}: {exc}") from exc

    return job_name
