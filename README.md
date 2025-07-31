# Sethlans Reborn - Distributed Blender Rendering System 🚀

![CI/CD](https://github.com/dryad-naiad-software/sethlans_reborn/actions/workflows/python-ci.yml/badge.svg)
![Python](https://img.shields.io/badge/Python-3.12+-blue)
![Django](https://img.shields.io/badge/Django-5.2-green)
![License](https://img.shields.io/badge/License-GPLv2-blue)

Sethlans Reborn is a powerful, distributed rendering system designed to accelerate Blender workflows by leveraging a network of worker machines. It consists of a central Django manager and multiple standalone Python worker agents that poll for jobs, render them, and return the results.

---
## Core Features

* **RESTful API**: A comprehensive API built with Django Rest Framework for managing projects, assets, workers, and render jobs.
* **Distributed Rendering**: Standalone Python worker agents can be deployed on any machine (Windows, macOS, Linux) to pick up and process render jobs.
* **Automatic Blender Management**: Workers can dynamically discover, download, cache, and verify any required Blender version on-demand.
* **Advanced Render Configuration**: Submit jobs with specific render engines (Cycles, Eevee), device preferences (CPU, GPU, ANY), and override any Blender setting (e.g., sample count, resolution).
* **Tiled Rendering**: Automatically split high-resolution still frames and animation sequences into a grid of tiles for parallel rendering, with automatic assembly of the final image(s).
* **Smart Job Filtering**: The manager ensures that jobs requiring a GPU are only offered to workers that have reported GPU capabilities.

---
## Architecture

The system is split into two main components:

### 1. Django Manager (Backend)
The central hub of the system.
* **Responsibilities**: Manages the database (projects, assets, jobs), provides the REST API, spawns child jobs for animations and tiled renders, and assembles the final output images from completed tiles.
* **Technology**: Django, Django Rest Framework, SQLite (for development).

### 2. Python Worker Agent (Client)
A standalone application that runs on each rendering machine.
* **Responsibilities**: Polls the manager for available jobs, downloads required `.blend` assets, manages local Blender installations, executes the render via a subprocess, and uploads the final output.
* **Technology**: Python, Requests, Psutil.

---
## Getting Started

### Prerequisites
* Git
* Python 3.12+

### Manager Setup
1.  **Clone the repository:**
    ```bash
    git clone https://github.com/dryad-naiad-software/sethlans_reborn.git
    cd sethlans_reborn
    ```
2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```
3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
4.  **Run database migrations:**
    ```bash
    python manage.py migrate
    ```
5.  **Start the development server:**
    ```bash
    python manage.py runserver
    ```
    The API will be available at `http://127.0.0.1:8000/api/`.

### Worker Agent Setup
The worker can be run on the same machine or any other machine on the network.

1.  **Clone the repository** (if on a separate machine).
2.  **Create and activate a virtual environment.**
3.  **Install worker-specific dependencies:**
    ```bash
    pip install -r sethlans_worker_agent/requirements_worker.txt
    ```
4.  **Run the agent:**
    ```bash
    python -m sethlans_worker_agent.agent
    ```
    You can specify a logging level with the `--loglevel` flag:
    ```bash
    python -m sethlans_worker_agent.agent --loglevel DEBUG
    ```

---
## Development & Testing

This project uses `pytest` for testing.

* **Run Unit Tests**:
    ```bash
    pytest -m "not e2e"
    ```
* **Run End-to-End (E2E) Tests**:
    *These tests are long-running, as they download Blender and execute real render jobs.*
    ```bash
    pytest -m "e2e"
    ```

---
## License
This project is licensed under the GNU General Public License v2.0. See the headers of the source files for more details.