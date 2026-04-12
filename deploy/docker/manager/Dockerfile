# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans

WORKDIR /app

COPY manager/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY manager/ /app/

# Volumes for persistent state
VOLUME ["/data", "/config", "/certs"]

# Env defaults for Docker deployment
ENV DJANGO_SETTINGS_MODULE=sethlans_manager.settings \
    SETHLANS_DB_NAME=/data/db.sqlite3 \
    SETHLANS_MEDIA_ROOT=/data/media \
    SETHLANS_TLS_CERT_FILE="" \
    SETHLANS_TLS_KEY_FILE="" \
    SETHLANS_SECURITY_DEBUG=false

# When no BYO cert is provided, run_manager.py auto-generates into tls/.
# We override get_tls_dir to use /data/tls/ so the cert persists across
# container restarts. This is handled by setting SETHLANS_TLS_DATA_DIR.
ENV SETHLANS_TLS_DATA_DIR=/data/tls

EXPOSE 8080

RUN mkdir -p /data /config /certs && chown -R sethlans:sethlans /data /config /certs
USER sethlans

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsk https://localhost:8080/api/health/ || exit 1

ENTRYPOINT ["python", "run_manager.py"]
