# Runtime image for the ephemeral code-interpreter containers (spec §4.5).
#
# Every run is a *fresh* container, hardened by `docker run` flags in
# backend/app/sandbox/manager.py:
#   --network none (no internet), --read-only + --tmpfs /tmp (64 MB),
#   --memory 1g, --cpus 2, --pids-limit 256, --user 65534:65534,
#   --cap-drop ALL, --security-opt no-new-privileges, --rm (destroyed after).
# The script being run is injected via stdin (`python -`), so nothing needs
# to be mounted into the container.
FROM python:3.12-slim

# Standard scientific computing stack + HTTP client (spec §4.5). Note:
# `requests` is installed per spec but cannot reach the network — the
# container has `--network none`.
RUN pip install --no-cache-dir pandas numpy matplotlib requests

# The runtime drops to the unprivileged `nobody` user (65534) via
# `docker run --user 65534:65534`; nothing here runs as root at rest.
CMD ["python", "--version"]
