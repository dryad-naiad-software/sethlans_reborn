# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

FROM python:3.12-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans

WORKDIR /app

COPY worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/ /app/

VOLUME ["/data", "/config"]

ENV SETHLANS_WORKER_DATA_DIR=/data \
    SETHLANS_IDLE_DETECTION_ENABLED=false \
    SETHLANS_WORKER_UI_ENABLED=true \
    SETHLANS_FORCE_CPU_ONLY=true

EXPOSE 8081

RUN mkdir -p /data /config && chown -R sethlans:sethlans /data /config
USER sethlans

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs http://localhost:8081/api/status || exit 1

ENTRYPOINT ["python", "run_worker.py"]
