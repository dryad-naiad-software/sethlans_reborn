# Sethlans Reborn - Distributed Blender Rendering System

![CI/CD](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12+-blue)
![Django](https://img.shields.io/badge/Django-5.2-green)
![License](https://img.shields.io/badge/License-GPLv2-blue)

Sethlans Reborn is a distributed rendering system that accelerates Blender workflows by farming render jobs across a network of machines. A central Django manager orchestrates work while standalone Python worker agents handle the rendering.

---

## Core Features

- **Distributed Rendering** - Deploy worker agents on any machine (Windows, macOS, Linux) to process render jobs in parallel across your network.
- **Tiled Rendering** - Split high-resolution frames into a grid of tiles (2x2 up to 5x5) for parallel rendering, with automatic assembly of the final image.
- **Animation Support** - Render animation sequences frame-by-frame across workers, with optional per-frame tiling for complex scenes.
- **Automatic Blender Management** - Workers discover, download, verify (SHA256), and cache any required Blender version on demand. Supports versions 4.0 through 4.5 LTS.
- **GPU-Aware Job Routing** - Jobs requiring GPU rendering are only offered to workers with GPU capabilities. Supports CUDA, OptiX, HIP, Metal, and oneAPI backends.
- **GPU Split Mode** - Workers can run GPU and CPU renders simultaneously, with thread-safe resource locking and automatic CPU thread scaling.
- **Project Management** - Organize jobs and assets into projects with pause/resume control over all associated work.
- **RESTful API** - Full API built with Django REST Framework, with interactive Swagger documentation.

---

## Architecture

```
 +--------------------+         REST API (port 7075)        +--------------------+
 |   Django Manager   | <----------------------------------> |   Worker Agent 1   |
 |                    |                                      +--------------------+
 |  - Project/Asset   |                                      +--------------------+
 |    management      | <----------------------------------> |   Worker Agent 2   |
 |  - Job scheduling  |                                      +--------------------+
 |  - Tile assembly   |                                      +--------------------+
 |  - API & docs      | <----------------------------------> |   Worker Agent N   |
 +--------------------+                                      +--------------------+
```

### Django Manager

The central hub that manages the database, provides the REST API, spawns child jobs for animations and tiled renders, and assembles final output images from completed tiles.

**Tech:** Django 5.2, Django REST Framework, django-filter, drf-spectacular, Pillow, SQLite

### Worker Agent

A standalone Python application that runs on each rendering machine. It polls for available jobs, downloads required `.blend` assets, manages local Blender installations, executes renders via subprocess, and uploads results.

**Tech:** Python, Requests, psutil, BeautifulSoup4

---

## Key Workflows

**Job lifecycle:**
`QUEUED` -> worker polls & claims (`RENDERING`) -> render -> upload output -> `DONE` / `ERROR`

**Tiled rendering:**
Parent TiledJob spawns an NxN grid of child Jobs -> each tile rendered independently -> signal auto-assembles final image -> tile cleanup

**Animations:**
Spawns one Job per frame (or NxN tile Jobs per frame if tiling is enabled) -> tracks per-frame progress -> `DONE` when all frames complete

---

## API Endpoints

All endpoints are under `/api/`. Interactive Swagger documentation is available at `/api/docs/`.

| Endpoint | Description |
|---|---|
| `projects/` | CRUD + pause/unpause actions |
| `assets/` | `.blend` file upload (multipart) |
| `jobs/` | Job distribution: poll, claim, cancel, update status, upload output |
| `animations/` | Create animations (auto-spawns child Jobs per frame) |
| `tiled-jobs/` | Create tiled jobs (auto-spawns tile Jobs in NxN grid) |
| `heartbeat/` | Worker registration and keep-alive |

**Job polling supports:** status filtering, GPU capability filtering, project pause exclusion, search by name, and ordering by submission time.

---

## Getting Started

### Prerequisites

- Python 3.12+
- Git

### Manager Setup

```bash
git clone https://github.com/dryad-naiad-software/sethlans_reborn.git
cd sethlans_reborn

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

Copy `manager.ini.example` to `manager.ini` and adjust settings as needed:

```ini
[server]
port = 7075

[security]
# secret_key = your-unique-secret-key-here
# debug = False
# allowed_hosts = sethlans.example.com, 192.168.1.100
```

Start the manager (applies migrations automatically):

```bash
python run_manager.py
```

The API is available at `http://127.0.0.1:7075/api/` and docs at `http://127.0.0.1:7075/api/docs/`.

### Worker Agent Setup

The worker can run on the same machine or any machine on the network.

```bash
pip install -r sethlans_worker_agent/requirements_worker.txt
```

Copy `sethlans_worker_agent/config.ini.example` to `sethlans_worker_agent/config.ini`:

```ini
[manager]
host = 127.0.0.1
port = 7075

[worker]
heartbeat_interval = 30
polling_interval = 5
cpu_threads = 0

[blender]
required_lts_version_series = 4.5
```

Start the agent:

```bash
python -m sethlans_worker_agent.agent
python -m sethlans_worker_agent.agent --loglevel DEBUG  # verbose logging
```

### Configuration Hierarchy

Both the manager and worker follow the same override pattern:

**Environment variables** > **INI file** > **Defaults**

Worker env vars use the pattern `SETHLANS_{SECTION}_{KEY}` (e.g., `SETHLANS_MANAGER_PORT`, `SETHLANS_WORKER_CPU_THREADS`). The manager port can be set via `SETHLANS_MANAGER_PORT`.

---

## Supported Render Configuration

| Setting | Options |
|---|---|
| Render Engine | Cycles, Eevee, Workbench |
| Device Preference | CPU, GPU, Any |
| Cycles Feature Set | Supported, Experimental |
| GPU Backends | OptiX, CUDA, HIP, Metal, oneAPI |
| Tiling Grids | None, 2x2, 3x3, 4x4, 5x5 |
| Blender Versions | 4.0.2, 4.1.1, 4.2.19 (LTS), 4.3.2, 4.4.3, 4.5.8 (LTS) |
| Render Settings | JSON overrides for any `bpy` property path (samples, resolution, etc.) |

---

## Development & Testing

### Running Tests

```bash
# Unit tests (fast, fully mocked)
pytest tests/unit

# End-to-end tests (downloads Blender, runs real renders)
pytest tests/e2e
```

### Linting

```bash
flake8 --max-line-length 127 --max-complexity 10
```

### CI/CD

The GitHub Actions workflow runs on every push and PR to `master` across three environments:

- **GitHub-hosted runners** - Ubuntu, Windows, macOS with Python 3.12 and 3.13
- **Self-hosted Linux GPU runner** - NVIDIA/AMD GPU testing with Python 3.13
- **Self-hosted Apple Silicon runner** - Metal GPU testing with Python 3.13

---

## Project Structure

```
config/                              Django settings, urls, wsgi/asgi
workers/                             Main Django app
  models/                            Project, Asset, Job, TiledJob, Animation, AnimationFrame, Worker
  views/                             DRF ViewSets (projects, jobs, assets, animations, tiled_jobs, heartbeat)
  serializers/                       DRF serializers by domain
  signals.py                         Post-save handlers (assembly, thumbnails, manifests)
  constants.py                       Enums: RenderEngine, RenderDevice, TilingConfiguration, etc.
  image_assembler.py                 Tile assembly logic (Pillow)
  image_utils.py                     Thumbnail generation
  manifest_generator.py              Project manifest file generation
sethlans_worker_agent/
  agent.py                           Entry point & main loop
  config.py                          Config hierarchy: env vars > config.ini > defaults
  api_handler.py                     HTTP communication with manager (retry + backoff)
  job_processor.py                   Job polling, claiming, GPU/CPU resource locking
  blender_executor.py                Blender subprocess execution
  render_script.py                   Blender Python script generation
  tool_manager.py                    Blender download/version management
  asset_manager.py                   .blend file caching
  hardware_detection.py              GPU/CPU detection via headless Blender
  system_monitor.py                  Worker registration and heartbeats
  utils/                             GPU detection script, release parser, file ops
tests/
  unit/                              Mock-based tests (worker agent + Django models/signals)
  e2e/                               Full integration tests (real Django server + worker + Blender)
```

---

## License

This project is licensed under the [GNU General Public License v2.0](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html).

Copyright (c) 2025 Dryad and Naiad Software LLC
