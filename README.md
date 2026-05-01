# Sethlans Reborn - Distributed Blender Rendering System

[![Docker Tests](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/docker-test.yml/badge.svg)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/docker-test.yml)
[![CI (Matrix)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci.yml/badge.svg)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci.yml)
[![CI (Self-Hosted GPU)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci-self-hosted.yml/badge.svg)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci-self-hosted.yml)
[![Docker Publish](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/docker-publish.yml)
[![Build Native Installers](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/build-installers.yml/badge.svg)](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/build-installers.yml)
![Python](https://img.shields.io/badge/Python-3.14+-blue)
![Django](https://img.shields.io/badge/Django-5.2-green)
![Angular](https://img.shields.io/badge/Angular-21-red)
![License](https://img.shields.io/badge/License-GPLv2+-blue)

Sethlans Reborn is a distributed rendering system that accelerates Blender workflows by farming render jobs across a network of machines. A central Django manager orchestrates work while standalone Python worker agents handle the rendering. An installable launcher brings it all together on a single workstation with a first-run setup wizard, tray helper, and opinionated defaults — or you can deploy manager and workers independently across a network via Docker or native installers.

---

## Core Features

- **Distributed Rendering** — Deploy worker agents on any machine (Windows, macOS, Linux) to process render jobs in parallel across your network.
- **Tiled Rendering** — Split high-resolution frames into a grid of tiles (2x2 up to 5x5) for parallel rendering, with automatic assembly of the final image.
- **Animation Support** — Render animation sequences frame-by-frame across workers, with optional per-frame tiling and FFmpeg video assembly (MP4 / WebM / MOV).
- **Automatic Blender Management** — Workers discover, download, verify (SHA256), and cache any required Blender version on demand. Supported versions are managed via a database registry, defaulting to the latest LTS.
- **GPU-Aware Job Routing** — Jobs requiring GPU rendering are only offered to workers with GPU capabilities. Supports CUDA, OptiX, HIP, Metal, and oneAPI backends.
- **GPU Split Mode** — Workers can run GPU and CPU renders simultaneously, with thread-safe resource locking and automatic CPU thread scaling.
- **HTTPS Transport** — All communication is encrypted via TLS. The manager auto-generates a self-signed certificate at first run, with support for bring-your-own certificates.
- **HMAC Enrollment** — Workers discover the manager via UDP multicast and enroll using an HMAC-signed handshake that exchanges a shared key for an API token and TLS certificate fingerprint.
- **Idle Detection & Scheduling** — Workers detect when a machine is idle before claiming jobs, yield mid-render when the artist returns, and honor configurable time windows for rendering.
- **First-Run Setup Wizard** — Browser-based setup wizard (topology selection, network/database config, admin user, FFmpeg/Blender download) via a launcher-managed lifecycle.
- **Tray Helper** — System tray icon exposes manager/worker status, setup-token copy, dashboard/wizard quick-launch, and graceful restart/quit. Cross-platform (Windows, macOS, Linux) via Qt.
- **Native Installers** — PyInstaller-based installers for Windows, macOS, and Linux bundle the launcher, manager, worker, and tray helper into a single app.
- **Docker Deployment** — Production-ready Dockerfiles and compose files for containerized manager and worker deployments, with NVIDIA and ROCm GPU support.
- **Angular Dashboard** — Angular 21 + Material UI frontend with project/job/worker/animation management, live status, and file upload.
- **Project Management** — Organize jobs and assets into projects with pause/resume control over all associated work.
- **RESTful API** — Full API built with Django REST Framework, with interactive Swagger documentation.

---

## Architecture

```
 +----------------+   launches   +----------------+   polls via HTTPS   +----------------+
 |    Launcher    |----+-------->|    Manager     |<-------------------|  Worker Agent  |
 |  (per-station) |    |         | (Django + DRF) |                    |   (standalone) |
 +----------------+    |         +----------------+                    +----------------+
         |             |                  ^                                     ^
         |             |                  | tray IPC                            |
         v             +----------------->+                                     |
  +----------------+                      |                                     |
  |  Tray Helper   |<---------------------+                                     |
  |  (Qt / PySide6)|  (status, notifications, menu actions)                     |
  +----------------+                                                            |
                                                                                |
                                              (multiple workers across network) |
```

### Launcher (`launcher/`)

First-run orchestrator. Detects whether setup is complete, launches the manager and tray helper, hosts the browser-based setup wizard on first run, enforces single-instance behavior, and coordinates graceful restarts via the tray IPC marker files.

### Django Manager (`manager/`)

The central hub. Serves the REST API, the Angular dashboard, and the setup wizard; manages projects, assets, jobs, animations, tiled jobs, and worker enrollment; spawns child jobs for animations and tiled renders; assembles final output images from completed tiles; and generates video from animation frames.

**Tech:** Django 5.2, Django REST Framework, django-filter, drf-spectacular, uvicorn (ASGI + TLS), Pillow, SQLite / PostgreSQL / MySQL, Angular 21 + Material UI

### Worker Agent (`worker/`)

A standalone Python application that runs on each rendering machine. It enrolls with the manager via HMAC handshake, pins the manager's TLS certificate fingerprint, polls for available jobs, manages local Blender installations, executes renders via subprocess, and uploads results. Supports idle detection, artist-return yielding, and scheduled rendering windows.

**Tech:** Python, Requests, psutil, cryptography

### Tray Helper (`shared/tray/`)

System-tray indicator with per-topology menus (manager-only, worker-only, or both). Shows live status, dispatches state-change notifications, copies the enrollment setup token to the clipboard, and exposes Open Dashboard / Open Setup Wizard / Restart / Quit actions. Built on **PySide6** (Qt) for unified cross-platform support.

#### Platform notes

- **Linux (GNOME):** GNOME 46+ on Wayland does not render tray icons natively. Install the [**AppIndicator and KStatusNotifierItem Support**](https://extensions.gnome.org/extension/615/appindicator-support/) GNOME Shell extension and enable it; the Sethlans tray icon will then appear in the top-right panel. KDE Plasma works out of the box — no extension required. X11 sessions (including XRDP) also work out of the box.
- **Windows 11:** by default, tray icons are hidden in the overflow flyout (the caret/chevron to the left of the clock). To keep Sethlans always visible, drag the icon from the flyout onto the taskbar tray, or toggle it on under **Settings → Personalization → Taskbar → Other system tray icons**.
- **macOS:** the tray helper runs menu-bar-only (`LSUIElement=true` in its `Info.plist`). It appears in the right side of the menu bar and intentionally has no Dock icon, no App Switcher entry, and no window on launch.

---

## Key Workflows

**First-run setup (single-workstation install):**
Launcher detects missing sentinel → starts manager in setup mode → opens the browser-based setup wizard → user picks topology (manager / worker / both), configures network/database/admin user, downloads FFmpeg and Blender → launcher writes sentinel and restarts into normal mode.

**Enrollment:**
Worker discovers manager via UDP multicast (or manual URL) → HMAC-signed enrollment exchanges shared key for API token + cert fingerprint → worker pins the manager's TLS certificate.

**Job lifecycle:**
`QUEUED` → worker polls & claims (`RENDERING`) → render → upload output → `DONE` / `ERROR`

**Tiled rendering:**
Parent TiledJob spawns an NxN grid of child Jobs → each tile rendered independently → signal auto-assembles final image → tile cleanup.

**Animations:**
Spawns one Job per frame (or NxN tile Jobs per frame if tiling is enabled) → tracks per-frame progress → optional FFmpeg video assembly → `DONE` when all frames complete.

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
| `system/shutdown/` | System control |
| `stats/` | Dashboard statistics |
| `setup/` | First-run wizard endpoints (topology, network, database, admin, downloads, verify) |
| `manager-defaults/` | Default Blender version / render engine for enrolled workers |

---

## Getting Started

### Option A: Native Installer (recommended for end users)

Download the installer for your platform from the [Releases](https://github.com/dryad-naiad-software/sethlans_reborn/releases) page. The installer bundles the launcher, manager, worker, and tray helper into a single app. On first run, a browser-based setup wizard walks you through topology selection (manager-only / worker-only / both), admin user creation, and Blender download.

### Option B: Docker (production / server deployment)

See [Docker Deployment](#docker-deployment) below.

### Option C: From Source (development)

#### Prerequisites

- Python 3.14+
- Node.js 20+ (for the Angular frontend)
- Git

#### Setup

```bash
git clone https://github.com/dryad-naiad-software/sethlans_reborn.git
cd sethlans_reborn

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -r manager/requirements.txt -r worker/requirements.txt -r requirements-dev.txt
```

#### Running via the launcher (includes tray + setup wizard)

```bash
python launcher/run_launcher.py
```

On first run, the launcher opens the setup wizard in your browser at `https://127.0.0.1:8080/setup/`. Subsequent runs bring the manager and tray helper up automatically.

#### Running the manager directly (no launcher / wizard)

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

#### Running the worker directly

```bash
python worker/run_worker.py
```

On first run, the worker launches an enrollment wizard that:
1. Discovers the manager via UDP multicast (or accepts a manual URL)
2. Prompts for the enrollment key displayed in the manager UI
3. Exchanges the key for an API token and pins the manager's TLS certificate

For unattended / Docker deployments, set environment variables instead:

| Env Var | Description |
|---|---|
| `SETHLANS_MANAGER_HOST` | Manager hostname / IP |
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

Frontend tests (Angular / Karma):

```bash
cd manager/frontend
npm install
npm test
```

### Linting

```bash
flake8 --max-line-length 127 --max-complexity 10
```

### Building Native Installers

PyInstaller specs and per-platform packaging scripts live under `packaging/`:

```bash
# Windows
bash tools/build_windows_installer.sh

# macOS / Linux: see packaging/macos/ and packaging/linux/
```

---

## Project Structure

```
launcher/                                Bootstrap launcher (first-run, tray, lifecycle)
  run_launcher.py                        Entry point for native installers
  orchestration.py                       Manager + tray subprocess supervision
  setup_helpers.py                       Port detection, atomic INI writes, setup token
manager/                                 Django manager (server)
  sethlans_manager/                      Django settings package (settings, urls, asgi)
  workers/                               Main Django app
    models/                              Project, Asset, Job, TiledJob, Animation, Worker, etc.
    views/                               DRF views (one file per resource/domain)
    serializers/                         DRF serializers by domain
    services/                            Pure-function business logic (setup, downloads, sentinel)
    signals.py                           Post-save handlers (assembly, thumbnails, manifests)
    constants.py                         Enums: RenderEngine, RenderDevice, TilingConfiguration, etc.
    image_assembler.py                   Tile assembly logic (Pillow)
  frontend/                              Angular 21 SPA (Material UI)
  run_manager.py                         Manager entry point (uvicorn + TLS)
  requirements.txt                       Manager runtime deps
worker/                                  Worker agent (client)
  sethlans_worker_agent/                 Python package
    agent.py                             Entry point & main loop
    job_processor.py                     Job polling, claiming, resource locking
    blender_executor.py                  Blender subprocess execution
    tool_manager.py                      Blender download/version management
    asset_manager.py                     .blend file caching
    system_monitor.py                    GPU/CPU detection, worker registration
  run_worker.py                          Worker entry point
  requirements.txt                       Worker runtime deps
shared/                                  Shared modules across launcher/manager/worker/tray
  tray/                                  System tray helper (PySide6 / Qt)
  frozen_paths.py                        OS-appropriate per-user data dir resolution
  tls_utils.py                           TLS certificate helpers
  run_tray.py                            Tray entry point
packaging/                               Native installer specs + per-platform scripts
  pyinstaller/                           PyInstaller .spec files (launcher, tray helper)
  windows/, macos/, linux/               Per-platform build / installer helpers
deploy/                                  Docker deployment
  docker/                                Dockerfiles, compose files, .env.example
tests/                                   All tests (three-tier pipeline)
  unit/                                  Fast, mocked unit tests
  integration/                           API endpoints, signals, component interactions
  e2e/                                   Full system tests (real server + worker + Blender)
tools/                                   Project scripts (test runners, installer builds, dev utilities)
requirements-build.txt                   Build-time deps (PyInstaller, PySide6-Essentials, requests, psutil)
requirements-dev.txt                     Test / dev deps (pytest, pytest-qt, flake8)
```

---

## License

This project is licensed under the [GNU General Public License v2.0 or later](https://www.gnu.org/licenses/old-licenses/gpl-2.0.en.html). See [LICENSE](LICENSE) for the full text.

The tray helper links against Qt (PySide6, LGPLv3) — the combined binary distribution is licensed as GPL-3.0-or-later under the "or later" clause. Project SPDX tag remains `GPL-2.0-or-later`; Qt attribution is bundled in the installer's `licenses/` directory and surfaced via the tray's "About Sethlans" menu action.

Copyright (c) 2025 Dryad and Naiad Software LLC
