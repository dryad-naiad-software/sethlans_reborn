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

# The import check fails the build if a database driver did not install
# (PyMySQL for MySQL and MariaDB, psycopg for PostgreSQL).
COPY manager/requirements.txt /tmp/manager-requirements.txt
RUN pip install --no-cache-dir -r /tmp/manager-requirements.txt && \
    python -c "import pymysql, psycopg"

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

# All manager state lives in /data (#297): the settings file
# (/data/sethlans.ini, optional), the TLS cert (/data/tls, so the
# fingerprint survives a new container), the secret key file, FFmpeg
# (/data/bin/ffmpeg), the collected static files (/data/staticfiles) and
# the logs (/data/logs). The manager resolves each of these through this
# one setting. The entrypoint sets /data to 0700.
ENV SETHLANS_MANAGER_DATA_DIR=/data

# Caddy: path to the static Caddyfile (above). Its presence tells the
# Caddy supervisor to use this file instead of writing one.
ENV SETHLANS_MANAGER_CADDYFILE_PATH=/etc/caddy/Caddyfile

EXPOSE 8080

# The runtime user owns the volumes (static files are collected into
# /data/staticfiles on every start). /app/manager itself and the code
# stay root-owned: /app/manager is first on sys.path, and a writable
# code dir would let planted modules run on every restart.
RUN mkdir -p /data /config /certs && \
    chown sethlans:sethlans /data /config /certs
USER sethlans

# Probes the port Caddy listens on: SETHLANS_MANAGER_PORT, else
# /data/sethlans.ini [server] port, else 8080 (#298).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "/app/launcher/container_healthcheck.py"]

# The entrypoint checks /data is writable (exit 3 with the fix if not),
# starts Caddy (TLS on 8080 by default) in front of the manager's
# loopback Waitress listeners, runs the manager as a child process, and
# stops both on SIGTERM. The container exits when the manager does.
ENTRYPOINT ["python", "/app/launcher/container_entrypoint.py"]
