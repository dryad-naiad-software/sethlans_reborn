# syntax=docker/dockerfile:1.4
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

FROM python:3.14-slim-bookworm

# TARGETARCH is populated by BuildKit/buildx during multi-arch builds
# (values: amd64, arm64). Used to select the correct pre-fetched Caddy
# binary from the build context's .tmp/caddy/linux-<arch>/ directory.
ARG TARGETARCH

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans

COPY manager/requirements.txt /tmp/manager-requirements.txt
RUN pip install --no-cache-dir -r /tmp/manager-requirements.txt

# Pre-fetched by CI (tools/fetch_caddy.py --multi-arch linux-amd64,linux-arm64).
# TARGETARCH is BuildKit-provided. Placed where
# shared.frozen_paths.get_caddy_path() resolves in source mode
# (<app root>/.venv-build/caddy/caddy), as in the worker images.
COPY --chmod=0755 .tmp/caddy/linux-${TARGETARCH}/caddy /app/.venv-build/caddy/caddy

# Static Caddyfile for container operation. The Caddy supervisor sees
# SETHLANS_MANAGER_CADDYFILE_PATH (below) and uses this file verbatim;
# Caddy resolves the {$VAR} placeholders from the env the supervisor
# gives it.
COPY deploy/docker/manager/Caddyfile /etc/caddy/Caddyfile

# Same layout as the source tree: the manager finds the repo-root
# shared/ package as the parent of manager/ (issue #296), and the
# container entrypoint lives in launcher/.
COPY shared/ /app/shared/
COPY launcher/ /app/launcher/
COPY manager/ /app/manager/

WORKDIR /app/manager

# Volumes for persistent state
VOLUME ["/data", "/config", "/certs"]

# Env defaults for Docker deployment
ENV DJANGO_SETTINGS_MODULE=sethlans_manager.settings \
    PYTHONUNBUFFERED=1 \
    SETHLANS_DB_NAME=/data/db.sqlite3 \
    SETHLANS_MEDIA_ROOT=/data/media \
    SETHLANS_TLS_CERT_FILE="" \
    SETHLANS_TLS_KEY_FILE="" \
    SETHLANS_SECURITY_DEBUG=false

# Without a BYO cert, the manager generates a self-signed one in
# SETHLANS_TLS_DATA_DIR so it (and its fingerprint) survive restarts.
ENV SETHLANS_TLS_DATA_DIR=/data/tls

# Caddy: path to the static Caddyfile (above). Its presence tells the
# Caddy supervisor to use this file instead of writing one.
ENV SETHLANS_MANAGER_CADDYFILE_PATH=/etc/caddy/Caddyfile

EXPOSE 8080

# Run from source, the manager writes its logs and collected static
# files under its own directory, so only those two subdirectories are
# given to the runtime user. /app/manager itself and the code stay
# root-owned: /app/manager is first on sys.path, and a writable code
# dir would let planted modules run on every restart. (The entrypoint
# tells the manager not to write broadcaster_params.json there.)
RUN mkdir -p /data /config /certs /app/manager/logs /app/manager/staticfiles && \
    chown sethlans:sethlans /data /config /certs \
        /app/manager/logs /app/manager/staticfiles
USER sethlans

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsk https://localhost:8080/api/health/ || exit 1

# The entrypoint starts Caddy (TLS on 8080) in front of the manager's
# loopback Waitress listeners, runs the manager as a child process, and
# stops both on SIGTERM. The container exits when the manager does.
ENTRYPOINT ["python", "/app/launcher/container_entrypoint.py"]
