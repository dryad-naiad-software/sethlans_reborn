# Sethlans Reborn - Distributed Blender Rendering System

![CI/CD](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci.yml/badge.svg)
![Docker](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/docker-publish.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12+-blue)
![Django](https://img.shields.io/badge/Django-5.2-green)
![License](https://img.shields.io/badge/License-GPLv2-blue)

Sethlans Reborn is a distributed rendering system that accelerates Blender workflows by farming render jobs across a network of machines. A central Django manager orchestrates work while standalone Python worker agents handle the rendering.

---

## Core Features

- **Distributed Rendering** - Deploy worker agents on any machine (Windows, macOS, Linux) to process render jobs in parallel across your network.
- **Tiled Rendering** - Split high-resolution frames into a grid of tiles (2x2 up to 5x5) for parallel rendering, with automatic assembly of the final image.
- **Animation Support** - Render animation sequences frame-by-frame across workers, with optional per-frame tiling and FFmpeg video assembly (MP4/WebM/MOV).
- **Automatic Blender Management** - Workers discover, download, verify (SHA256), and cache any required Blender version on demand. Supported versions are managed via a database registry, defaulting to the latest LTS.
- **GPU-Aware Job Routing** - Jobs requiring GPU rendering are only offered to workers with GPU capabilities. Supports CUDA, OptiX, HIP, Metal, and oneAPI backends.
- **GPU Split Mode** - Workers can run GPU and CPU renders simultaneously, with thread-safe resource locking and automatic CPU thread scaling.
- **HTTPS Transport** - All communication is encrypted via TLS. The manager auto-generates a self-signed certificate at first run, with support for bring-your-own certificates.
- **HMAC Enrollment** - Workers discover the manager via UDP multicast and enroll using an HMAC-signed handshake that exchanges a shared key for an API token and TLS certificate fingerprint.
- **Idle Detection & Scheduling** - Workers detect when a machine is idle before claiming jobs, yield mid-render when the artist returns, and honor configurable time windows for rendering.
- **Docker Deployment** - Production-ready Dockerfiles and compose files for containerized manager and worker deployments, with NVIDIA and ROCm GPU support.
- **Project Management** - Organize jobs and assets into projects with pause/resume control over all associated work.
- **RESTful API** - Full API built with Django REST Framework, with interactive Swagger documentation.

---

## Architecture

```
 +--------------------+       HTTPS (port 8080)        +--------------------+
 |   Django Manager   | <-----------------------------> |   Worker Agent 1   |
 |                    |                                 +--------------------+
 |  - Project/Asset   |                                 +--------------------+
 |    management      | <-----------------------------> |   Worker Agent 2   |
 |  - Job scheduling  |                                 +--------------------+
 |  - Tile assembly   |                                 +--------------------+
 |  - API & docs      | <-----------------------------> |   Worker Agent N   |
 +--------------------+                                 +--------------------+
```

### Django Manager

The central hub that manages the database, provides the REST API, spawns child jobs for animations and tiled renders, assembles final output images from completed tiles, and generates video from animation frames.

**Tech:** Django 5.2, Django REST Framework, django-filter, drf-spectacular, uvicorn (ASGI + TLS), Pillow, SQLite

### Worker Agent

A standalone Python application that runs on each rendering machine. It enrolls with the manager via HMAC handshake, pins the manager's TLS certificate fingerprint, polls for available jobs, manages local Blender installations, executes renders via subprocess, and uploads results. Supports idle detection, artist-return yielding, and scheduled rendering windows.

**Tech:** Python, Requests, psutil, cryptography

---

## Key Workflows

**Enrollment:**
Worker discovers manager via UDP multicast (or manual URL) -> HMAC-signed enrollment exchanges shared key for API token + cert fingerprint -> worker pins the manager's TLS certificate

**Job lifecycle:**
`QUEUED` -> worker polls & claims (`RENDERING`) -> render -> upload output -> `DONE` / `ERROR`

**Tiled rendering:**
Parent TiledJob spawns an NxN grid of child Jobs -> each tile rendered independently -> signal auto-assembles final image -> tile cleanup

**Animations:**
Spawns one Job per frame (or NxN tile Jobs per frame if tiling is enabled) -> tracks per-frame progress -> optional FFmpeg video assembly -> `DONE` when all frames complete

---

## API Endpoints

All endpoints are under `/api/`. Interactive Swagger documentation is available at `/api/docs/`, OpenAPI schema at `/api/schema/`.

| Endpoint | Description |
|---|---|
| `projects/` | CRUD + pause/unpause actions |
| `assets/` | `.blend` file upload (multipart) |
| `jobs/` | Job distribution: poll, claim, cancel, yield-requeue, update status, upload output |
| `animations/` | Create animations (auto-spawns child Jobs per frame) |
| `tiled-jobs/` | Create tiled jobs (auto-spawns tile Jobs in NxN grid) |
| `heartbeat/` | Worker keep-alive + yield event reporting |
| `enroll/` | HMAC-bootstrapped worker enrollment (anonymous) |
| `health/` | Unauthenticated health check for Docker/load balancer probes |
| `supported-versions/` | Blender version registry |
| `queue-settings/` | Queue configuration |
| `auth/` | Session auth (CSRF, login, logout, user info, enrollment key rotation) |
| `system/` | Shutdown, system info |
| `stats/` | Dashboard statistics |

---

## Getting Started

### Prerequisites

- Python 3.12+
- Git

### Manager Setup

```bash
git clone https://github.com/dryad-naiad-software/sethlans_reborn.git
cd sethlans_reborn

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r manager/requirements.txt
```

Start the manager (applies migrations and auto-generates a TLS certificate on first run):

```bash
python manager/run_manager.py          # Production mode
python manager/run_manager.py --dev    # Dev mode (hot reload)
```

The API is available at `https://127.0.0.1:8080/api/` and docs at `https://127.0.0.1:8080/api/docs/`.

Optional configuration via `manager/manager.ini` or environment variables:

| Env Var | Description | Default |
|---|---|---|
| `SETHLANS_MANAGER_HOST` | Bind address | `0.0.0.0` |
| `SETHLANS_MANAGER_PORT` | Bind port | `8080` |
| `SETHLANS_TLS_CERT_FILE` | BYO TLS certificate path | (auto-generated) |
| `SETHLANS_TLS_KEY_FILE` | BYO TLS key path | (auto-generated) |
| `SETHLANS_SECURITY_SECRET_KEY` | Django secret key | (generated default) |

### Worker Agent Setup

The worker can run on the same machine or any machine on the network.

```bash
pip install -r worker/requirements.txt
python worker/run_worker.py
```

On first run, the worker launches an enrollment wizard that:
1. Discovers the manager via UDP multicast (or accepts a manual URL)
2. Prompts for the enrollment key displayed in the manager UI
3. Exchanges the key for an API token and pins the manager's TLS certificate

For unattended/Docker deployments, set environment variables instead:

| Env Var | Description |
|---|---|
| `SETHLANS_MANAGER_HOST` | Manager hostname/IP |
| `SETHLANS_MANAGER_PORT` | Manager port |
| `SETHLANS_WORKER_ENROLLMENT_KEY` | Shared enrollment key |

Worker configuration is stored in a JSON config file at the OS-appropriate per-user data directory.

---

## Docker Deployment

Production Docker images are available for containerized deployments.

```bash
cd deploy/docker
cp .env.example .env
# Edit .env — set SETHLANS_SECURITY_SECRET_KEY (required)

# Manager + CPU worker
docker compose up -d

# Manager + NVIDIA GPU worker
docker compose -f docker-compose.yml -f docker-compose.gpu-nvidia.yml up -d

# Manager + ROCm GPU worker
docker compose -f docker-compose.yml -f docker-compose.gpu-rocm.yml up -d

# Manager only
docker compose up -d manager
```

Images are published to GitHub Container Registry:
- `ghcr.io/dryad-naiad-software/sethlans-manager`
- `ghcr.io/dryad-naiad-software/sethlans-worker-cpu`
- `ghcr.io/dryad-naiad-software/sethlans-worker-nvidia`
- `ghcr.io/dryad-naiad-software/sethlans-worker-rocm`

All containers run as non-root (uid 1000). Manager and CPU worker images support `linux/amd64` and `linux/arm64`.

---

## Supported Render Configuration

| Setting | Options |
|---|---|
| Render Engine | Cycles, Eevee, Workbench |
| Device Preference | CPU, GPU, Any |
| Cycles Feature Set | Supported, Experimental |
| GPU Backends | OptiX, CUDA, HIP, Metal, oneAPI |
| Tiling Grids | None, 2x2, 3x3, 4x4, 5x5 |
| Blender Versions | Managed via database registry (defaults to latest LTS) |
| Render Settings | JSON overrides for any `bpy` property path (samples, resolution, etc.) |
| Output Formats | PNG, JPEG, TIFF, EXR, HDR, TGA |
| Video Assembly | MP4, WebM, MOV (via FFmpeg) |

---

## Development & Testing

### Install Dev Dependencies

```bash
pip install -r manager/requirements.txt -r worker/requirements.txt -r requirements-dev.txt
```

### Running Tests

```bash
# Unit tests (fast, fully mocked)
pytest tests/unit

# Integration tests (API endpoints, signals, component interactions)
pytest tests/integration

# E2E tests (slow — downloads Blender, runs real renders)
pytest tests/e2e
```

### Linting

```bash
flake8 --max-line-length 127 --max-complexity 10
```

---

## Project Structure

```
manager/                                 Django manager (server)
  sethlans_manager/                      Django settings package (settings, urls, asgi)
  workers/                               Main Django app
    models/                              Project, Asset, Job, TiledJob, Animation, Worker, etc.
    views/                               DRF views (one file per resource/domain)
    serializers/                         DRF serializers by domain
    signals.py                           Post-save handlers (assembly, thumbnails, manifests)
    constants.py                         Enums: RenderEngine, RenderDevice, TilingConfiguration, etc.
    image_assembler.py                   Tile assembly logic (Pillow)
  frontend/                              Angular frontend (Material UI)
  run_manager.py                         Manager entry point (uvicorn + TLS)
  requirements.txt                       Manager runtime deps
worker/                                  Worker agent (client)
  sethlans_worker_agent/                 Python package
    agent.py                             Entry point & main loop
    config_store/                        JSON config persistence (per-OS data dirs)
    idle_detection/                      Per-platform idle detection (Windows/macOS/Linux)
    enrollment_client.py                 HMAC-signed enrollment
    job_processor.py                     Job polling, claiming, resource locking
    blender_executor.py                  Blender subprocess execution
    tool_manager.py                      Blender download/version management
    asset_manager.py                     .blend file caching
    hardware_detection.py                GPU/CPU detection
  run_worker.py                          Worker entry point
  requirements.txt                       Worker runtime deps
deploy/                                  Docker deployment
  docker/                                Dockerfiles, compose files, .env.example
tests/                                   All tests (three-tier pipeline)
  unit/                                  Fast, mocked unit tests
  integration/                           API endpoints, signals, component interactions
  e2e/                                   Full system tests (real server + worker + Blender)
tools/                                   Project scripts (test runners, dev utilities)
```

---

## License

This project is licensed under the [GNU General Public License v2.0](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html).

Copyright (c) 2025 Dryad and Naiad Software LLC
