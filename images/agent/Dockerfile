FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 agent && \
    useradd --uid 1000 --gid agent --shell /bin/sh --create-home agent

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir strands-agents

# Tool availability is controlled at runtime via NUBI_TOOLS env var
# e.g. NUBI_TOOLS=shell,git,file_read,file_write
ENV NUBI_TOOLS=""

USER agent

ENTRYPOINT ["python", "-m", "nubi.agents"]
