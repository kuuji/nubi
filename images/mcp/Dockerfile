FROM python:3.12-slim AS build

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir build \
    && pip install --no-cache-dir .


FROM python:3.12-slim

RUN pip install --no-cache-dir mcp kubernetes

RUN groupadd --gid 1000 nubi && \
    useradd --uid 1000 --gid nubi --shell /bin/sh --create-home nubi

WORKDIR /app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build /build/src/ ./src/

USER nubi

CMD ["python", "-m", "nubi.mcp"]
