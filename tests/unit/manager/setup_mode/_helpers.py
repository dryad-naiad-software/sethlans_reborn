# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers shared by the setup-mode unit tests (no fixtures here)."""
import copy
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
MANAGER_DIR = REPO_ROOT / "manager"
RUN_MANAGER = MANAGER_DIR / "run_manager.py"

TOKEN = "setup-token-for-tests-0123456789abcdef"
GOOD_PASSWORD = "Tr1cky-Setup-Pass!"
FAKE_HASH = "pbkdf2_sha256$1000000$fakesalt$fakehash="
LAN_ADDRESS = "192.168.77.10"
SETUP_PORT = 18443
RESERVED_PORTS = {8090, 8088}

# Env vars that would change a database, data folder or port answer.
MOVING_ENV = (
    "SETHLANS_DATABASE_URL", "SETHLANS_DB_ENGINE", "SETHLANS_DB_NAME", "SETHLANS_DB_HOST",
    "SETHLANS_DB_PORT", "SETHLANS_DB_USER", "SETHLANS_DB_PASSWORD", "SETHLANS_DB_PASSWORD_FILE",
    "SETHLANS_DB_TLS", "SETHLANS_DB_TLS_CA_FILE", "SETHLANS_DB_TLS_MODE",
    "SETHLANS_MANAGER_DB_CONN_MAX_AGE", "SETHLANS_DATA_ROOT", "SETHLANS_MANAGER_DATA_DIR",
    "SETHLANS_WORKER_DATA_DIR", "SETHLANS_SETUP_PORT",
)


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def data_root(tmp_path: Path) -> str:
    return str(tmp_path / "sethlans-data")


def answers(tmp_path: Path, **over) -> dict:
    """A valid ``manager`` submit; ``over`` replaces whole top-level keys."""
    base = {
        "mode": "manager",
        "server": {"host": "0.0.0.0", "port": 18444},
        "data_folder": {"path": data_root(tmp_path)},
        "database": {"engine": "sqlite"},
        "admin": {"username": "boss", "email": "boss@example.com", "password": GOOD_PASSWORD},
    }
    base.update(copy.deepcopy(over))
    return base


def pg_database(**over) -> dict:
    base = {"engine": "postgresql", "name": "sethlans", "host": "127.0.0.1", "user": "seth",
            "password": "db-secret-pw"}
    base.update(over)
    return base


def child_env(tmp_path: Path, **extra) -> dict:
    """An environment for a child Python: repo on the path, every folder in ``tmp_path``."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("SETHLANS_") and key not in ("DJANGO_SETTINGS_MODULE", "PYTHONPATH")}
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    env.update({
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(MANAGER_DIR)]),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
        "SETHLANS_SETTINGS_DIR": str(settings_dir),
        "SETHLANS_SETTINGS_FILE": str(settings_dir / "sethlans.ini"),
        "PYTHONIOENCODING": "utf-8",
    })
    env.update(extra)
    return env


def open_session(client, remote_addr="127.0.0.1") -> str:
    """Authenticate ``client``; return the session token (the cookie is now set)."""
    response = client.post("/api/setup/auth/", {"token": TOKEN}, content_type="application/json",
                           REMOTE_ADDR=remote_addr)
    assert response.status_code == 200, response.content
    return response.json()["session_token"]


PYTHON = sys.executable
