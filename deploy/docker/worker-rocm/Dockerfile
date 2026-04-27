# syntax=docker/dockerfile:1.4
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

ARG ROCM_VERSION=6.3.1
FROM rocm/dev-ubuntu-24.04:${ROCM_VERSION}

ARG DEBIAN_FRONTEND=noninteractive

# TARGETARCH is populated by BuildKit/buildx during multi-arch builds
# (values: amd64, arm64). Used to select the correct pre-fetched Caddy
# binary from the build context's .tmp/caddy/linux-<arch>/ directory.
# Caddy is present-but-unused in the image until worker spec Phase 5
# wires launcher supervision.
ARG TARGETARCH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip curl xz-utils \
        libegl1 libgl1 libglu1-mesa \
        libx11-6 libxi6 libxrender1 libxkbcommon0 \
        libxxf86vm1 libxfixes3 libxcursor1 \
        libxinerama1 libxrandr2 libsm6 libice6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python

# Remove the default ubuntu user/group (UID/GID 1000) if present in the base image
RUN userdel -r ubuntu 2>/dev/null || true && \
    groupdel ubuntu 2>/dev/null || true && \
    groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans && \
    usermod -aG render,video sethlans

WORKDIR /app

# Use a virtual environment instead of --break-system-packages
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Pre-fetched by CI (tools/fetch_caddy.py --multi-arch linux-amd64,linux-arm64).
# TARGETARCH is BuildKit-provided; falls back to amd64 for classic docker build.
COPY --chmod=0755 .tmp/caddy/linux-${TARGETARCH}/caddy /usr/local/bin/caddy

# Static worker Caddyfile. caddy_supervisor reads SETHLANS_WORKER_CADDYFILE_PATH
# and skips its native template-and-write branch; Caddy resolves the
# {$VAR} placeholders from env at config-load time.
COPY deploy/docker/worker/Caddyfile /etc/caddy/Caddyfile

COPY worker/ /app/

# Repo-root shared/ package — worker imports from shared.cert_utils,
# shared.caddy_supervisor, shared.frozen_paths, and shared.version.
# /app is on sys.path (entrypoint is python /app/run_worker.py) so
# placing shared/ at /app/shared/ resolves the imports the same way
# as source mode. Issue #157.
COPY shared/ /app/shared/

VOLUME ["/data", "/config"]

ENV SETHLANS_WORKER_DATA_DIR=/data \
    SETHLANS_IDLE_DETECTION_ENABLED=false \
    SETHLANS_WORKER_UI_ENABLED=true \
    SETHLANS_WORKER_CADDYFILE_PATH=/etc/caddy/Caddyfile

EXPOSE 8081

RUN mkdir -p /data /config && chown -R sethlans:sethlans /data /config
USER sethlans

# -k: self-signed cert, localhost-only probe -- do not cargo-cult to network calls
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsk https://localhost:8081/api/health/ || exit 1

ENTRYPOINT ["python", "run_worker.py"]
