# Runtime image shared by two sandboxes (both driven via the host docker socket
# through backend/app/sandbox/*, never by docker-in-docker):
#
#   1. Ephemeral code-interpreter (spec §4.5) — a FRESH, hardened container:
#        --network none, --read-only + --tmpfs /tmp (64 MB), --memory 1g,
#        --cpus 2, --pids-limit 256, --user 65534:65534, --cap-drop ALL,
#        --security-opt no-new-privileges, --rm (destroyed after).
#        The script is injected via stdin (`python -`), so nothing is mounted.
#
#   2. Interactive terminal (VSCode-style dock) — a PERSISTENT container that
#        stays alive, is network-enabled (bridge), gets a writable /workspace,
#        and optionally a read+write bind of the project repo at /repo. Runs a
#        real shell via the baked PTY bridge below /ptymaster.py.
#
# The extra shell/tool packages are inert for profile (1) because that profile
# stays offline, read-only, and unprivileged — but they make profile (2) a real
# coding terminal (bash + git + curl + wget + common utilities).
FROM python:3.12-slim

# Standard scientific computing stack + HTTP client (spec §4.5).
RUN pip install --no-cache-dir pandas numpy matplotlib requests

# Shell + common developer tools for the interactive terminal profile.
# (bash is not in python:slim; git/curl/wget for real work; a small set of
#  utilities people expect in a VSCode terminal.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash less nano file findutils procps \
        git curl wget ca-certificates jq unzip zip \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# Node.js (matches frontend/Dockerfile's build stage) so frontend_lint /
# frontend_typecheck (npx eslint / npx tsc, app/repos/checks.py) work without
# an on-demand install on every use. checks.py still self-heals a container
# started from an older image (installs Node the same way, at runtime).
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Writable scratch dir for the terminal profile (backed by a named volume from
# the backend). Owned by 1000 (== the backend appuser / host clone owner) so the
# terminal_user (default 1000:1000) can write it; HOME points here so `~`, .pip,
# and .gitconfig land in persistent, writable space.
RUN mkdir -p /workspace && chown -R 1000:1000 /workspace
ENV HOME=/workspace

# Named user for the interactive-terminal profile (the container runs as
# `--user 1000:1000`). python:3.12-slim ships UID 1000 with NO /etc/passwd entry,
# so bash --login would resolve the prompt's `\u` to "I have no name!" (and
# `whoami` lies "root"). Creating a real user (uid==gid==1000, home /workspace,
# bash shell) fixes the prompt, `whoami`, and `$USER`.
#
# The one gotcha: a freshly-created account is password-LOCKED (shadow `!`), and
# PAM's account phase rejects `sudo` for a locked account even under NOPASSWD
# ("account validation failure, is your account locked?"). `usermod -p '*'`
# flips it to "unlocked, no password login" — the state under which `sudo -n`
# actually succeeds. `useradd`/`usermod` ship pre-installed (they come from the
# `passwd` package, already present in python:slim). Inert for the offline
# code-interpreter profile (it runs as 65534, never reads this user at all).
RUN set -eux; \
    groupadd -g 1000 qwen-chat; \
    useradd  -u 1000 -g 1000 -d /workspace -s /bin/bash qwen-chat; \
    usermod -p '*' qwen-chat; \
    id qwen-chat

# Passwordless sudo for the interactive-terminal profile (runs as 1000/qwen-chat).
# That profile is deliberately NOT hardened (writable root FS, no cap-drop, no
# no-new-privileges) so it is a real dev shell — full NOPASSWD lets it do
# `sudo apt-get install ...`, `sudo pip ...`, `sudo chown ...` with no prompt,
# the standard dev-container UX. Mode 0440 root:root; `visudo -cf` + `cat` are
# build-time assertions. Inert for the code-interpreter profile: it runs as 65534
# (not qwen-chat) under --read-only + --cap-drop ALL + --security-opt
# no-new-privileges, so neither the match nor any escalation applies there.
RUN set -eux; \
    printf 'qwen-chat ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/qwen-chat; \
    chown root:root /etc/sudoers.d/qwen-chat; \
    chmod 0440 /etc/sudoers.d/qwen-chat; \
    visudo -cf /etc/sudoers.d/qwen-chat; \
    cat /etc/sudoers.d/qwen-chat

# PTY bridge for the interactive terminal: `docker exec -i <c> python3 /ptymaster.py`.
# Baked into the image (not passed via `python -c "<code>"`) so the bridge source
# never crosses the CLI as a shell-quoted argument.
COPY bridge.py /ptymaster.py

# Default profile still reports the Python runtime (code-interpreter contract);
# the terminal profile overrides the command to `sleep infinity` via docker run.
CMD ["python", "--version"]
