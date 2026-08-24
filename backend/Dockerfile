FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

# git: repo clones for ingestion (Phase 2) + branch/commit/PR work (Phase 4)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# docker CLI (static binary, pinned to the host daemon's major version) for
# managing the ephemeral code-interpreter sandboxes over the host Docker
# socket (Phase 5). Only the client binary is installed — no daemon.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-29.6.2.tgz" \
         -o /tmp/docker.tgz \
    && tar -xzf /tmp/docker.tgz -C /tmp \
    && install -m 0755 /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker /tmp/docker.tgz

# Install deps first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

# UID 1000 is deterministic so the host-owned ./workspace volume is writable.
# gid 988 = the host's `docker` group (the socket is 0660 root:docker), so
# appuser can run the sandbox CLI against the daemon; keep in sync if the
# host group id ever changes.
RUN groupadd -g 988 docker \
    && useradd -m -u 1000 appuser \
    && usermod -aG docker appuser \
    && mkdir -p /srv/app/workspace \
    && chown -R appuser:appuser /srv/app
USER appuser

EXPOSE 8000

# Single worker: in-process login rate limiter (see app/api/auth.py)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
