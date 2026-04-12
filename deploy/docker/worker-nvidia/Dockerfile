# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
# SPDX-License-Identifier: GPL-2.0-or-later

FROM nvidia/cuda:12.6.0-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    ln -sf /usr/bin/python3 /usr/bin/python

RUN groupadd -g 1000 sethlans && \
    useradd -u 1000 -g 1000 -m -s /bin/bash sethlans

WORKDIR /app

# Use a virtual environment instead of --break-system-packages
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY worker/ /app/

VOLUME ["/data", "/config"]

ENV SETHLANS_WORKER_DATA_DIR=/data \
    SETHLANS_IDLE_DETECTION_ENABLED=false \
    SETHLANS_WORKER_UI_ENABLED=true \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

EXPOSE 8081

RUN mkdir -p /data /config && chown -R sethlans:sethlans /data /config
USER sethlans

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs http://localhost:8081/api/status || exit 1

ENTRYPOINT ["python", "run_worker.py"]
