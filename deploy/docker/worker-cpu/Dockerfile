# syntax=docker/dockerfile:1.4
# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

FROM python:3.14-slim-bookworm

# TARGETARCH is populated by BuildKit/buildx during multi-arch builds
# (values: amd64, arm64). Used to select the correct pre-fetched Caddy
# binary from the build context's .tmp/caddy/linux-<arch>/ directory.
# Caddy is present-but-unused in the image until worker spec Phase 5
# wires launcher supervision.
ARG TARGETARCH

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl xz-utils \
        libegl1 libgl1 libglu1-mesa \
        libx11-6 libxi6 libxrender1 libxkbcommon0 \
        libxxf86vm1 libxfixes3 libxcursor1 \
        libxinerama1 libxrandr2 libsm6 libice6 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans

WORKDIR /app

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

VOLUME ["/data", "/config"]

ENV SETHLANS_WORKER_DATA_DIR=/data \
    SETHLANS_IDLE_DETECTION_ENABLED=false \
    SETHLANS_WORKER_UI_ENABLED=true \
    SETHLANS_FORCE_CPU_ONLY=true \
    SETHLANS_WORKER_CADDYFILE_PATH=/etc/caddy/Caddyfile

EXPOSE 8081

RUN mkdir -p /data /config && chown -R sethlans:sethlans /data /config
USER sethlans

# -k: self-signed cert, localhost-only probe -- do not cargo-cult to network calls
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsk https://localhost:8081/api/health/ || exit 1

ENTRYPOINT ["python", "run_worker.py"]
