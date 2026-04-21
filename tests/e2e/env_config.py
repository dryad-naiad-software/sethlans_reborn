# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Environment configuration for E2E test subprocesses.

Provides env-dict builders for the Django manager and worker agent,
along with shared constants (paths, credentials, secrets).

Enrollment changes (worker-enrollment spec, FR-15/FR-24/FR-28/FR-32):

- ``generate_secrets()`` returns a valid Crockford base32 enrollment
  key by calling ``workers.enrollment_key.generate_key()``. The old
  implementation used ``secrets.token_urlsafe(32)`` which produces a
  43-character base64 string that is rejected by the manager's new
  ``normalize()`` format validation.

- ``build_worker_env()`` uses ``SETHLANS_WORKER_ENROLLMENT_KEY``
  (FR-32's unattended-wizard env var name), NOT the old
  ``SETHLANS_MANAGER_ENROLLMENT_KEY``. The "empty string to wipe stale
  state" pattern for ``api_token`` / ``cert_fingerprint`` has been
  removed because FR-28 now ignores those env vars entirely (they are
  the "post-enrollment credential triple" and are managed exclusively
  by the JSON config store).

- Worker data-dir isolation: the worker's JSON config store lives in a
  per-user OS data directory (``%LOCALAPPDATA%\\Sethlans\\worker`` on
  Windows, ``$XDG_DATA_HOME/sethlans/worker`` on Linux,
  ``~/Library/Application Support/Sethlans/worker`` on macOS). An E2E
  test that runs the real worker subprocess MUST redirect this to a
  per-test tmp path, otherwise (a) the user's real worker config gets
  clobbered and (b) stale state from a prior test leaks into the next.
  We redirect by overriding the platform-specific env var that
  ``worker.sethlans_worker_agent.config_store.paths.get_data_dir()``
  reads: ``LOCALAPPDATA`` on Windows, ``XDG_DATA_HOME`` on Linux,
  and ``HOME`` on macOS. (Option B in the spec task notes.)
"""

import os
import platform
import secrets as _py_secrets
import socket
from pathlib import Path

# ``workers`` is the manager-side Django app; ``pytest.ini`` puts
# ``manager/`` on the pythonpath, so ``workers.enrollment_key`` is
# importable from test code without any sys.path gymnastics.
from workers.enrollment_key import generate_key as _generate_crockford_key


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANAGE_PY = str(REPO_ROOT / "manager" / "manage.py")
RUN_MANAGER = str(REPO_ROOT / "manager" / "run_manager.py")
WORKER_ENTRY = str(REPO_ROOT / "worker" / "run_worker.py")

# Credentials for the test admin user.
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "testpass123!"
ADMIN_EMAIL = "admin@test.local"


def find_free_port():
    """Find and return a free TCP port using OS allocation."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def build_manager_env(
    db_path, media_root, enrollment_key, secret_key, port,
    *, tls_data_dir=None,
):
    """
    Build the environment dict for the manager subprocess.

    All secrets are passed exclusively via env vars -- never
    written to os.environ in the parent process.

    ``enrollment_key`` MUST be a Crockford base32 16-char key (or its
    hyphenated display form). ``normalize()`` accepts both; anything
    else will be rejected at manager startup with a non-zero exit.

    Post-Waitress-migration (Caddy+Waitress topology): ``port`` here is
    repurposed as the **public TLS port** that Caddy binds. Waitress
    itself listens plaintext on two additional loopback ports (public
    vhost + internal vhost); ``start_manager`` in ``process_manager.py``
    allocates those ports and injects the corresponding
    ``SETHLANS_MANAGER_WAITRESS_PORT_PUBLIC`` /
    ``SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL`` env vars on top of this
    dict before spawning the manager. ``SETHLANS_MANAGER_PORT`` is left
    pointing at the Caddy port for the banner label only; Waitress
    never reads it.

    ``tls_data_dir`` (optional): absolute path where the manager will
    write ``tls/cert.pem`` + ``tls/key.pem``. Pointing this at a per-
    test tmp directory keeps each run isolated and gives the Caddy
    subprocess a known cert/key path inside ``manager_data_dir`` (the
    Caddyfile template validates that the cert/key paths resolve
    underneath the data dir).
    """
    env = os.environ.copy()
    # Ensure the project root, manager/, and worker/ are on PYTHONPATH
    # so the subprocess can import shared.frozen_paths (project root),
    # sethlans_manager (manager/), and sethlans_worker_agent (worker/).
    pp = os.pathsep.join([
        str(REPO_ROOT),
        str(REPO_ROOT / "manager"),
        str(REPO_ROOT / "worker"),
    ])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{pp}{os.pathsep}{existing}" if existing else pp
    env.update({
        "DJANGO_SETTINGS_MODULE": "sethlans_manager.settings",
        "SETHLANS_DB_NAME": str(db_path),
        "SETHLANS_MEDIA_ROOT": str(media_root),
        "SETHLANS_SECURITY_SECRET_KEY": secret_key,
        "SETHLANS_SECURITY_DEBUG": "true",
        "SETHLANS_SECURITY_ENROLLMENT_KEY": enrollment_key,
        "SETHLANS_MANAGER_HOST": "127.0.0.1",
        "SETHLANS_MANAGER_PORT": str(port),
        "DJANGO_SUPERUSER_USERNAME": ADMIN_USERNAME,
        "DJANGO_SUPERUSER_PASSWORD": ADMIN_PASSWORD,
        "DJANGO_SUPERUSER_EMAIL": ADMIN_EMAIL,
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if tls_data_dir is None:
        # Default: place TLS cert/key inside the same per-test tmp
        # tree that owns the DB and media root. The Caddy subprocess
        # (started by ``start_manager``) validates that cert/key paths
        # resolve inside its ``manager_data_dir`` — anchoring them at
        # ``<tmp>/manager_data/tls/`` keeps that invariant true without
        # requiring every call site to pass the path explicitly.
        tls_data_dir = Path(db_path).resolve().parent / "manager_data" / "tls"
    tls_data_dir = Path(tls_data_dir)
    tls_data_dir.mkdir(parents=True, exist_ok=True)
    env["SETHLANS_TLS_DATA_DIR"] = str(tls_data_dir)
    return env


def _apply_worker_data_dir(env, worker_data_dir):
    """Point the worker's per-OS user-data dir at ``worker_data_dir``.

    The worker's ``config_store.paths.get_data_dir()`` resolves the per-OS
    data directory from well-known platform env vars. We override those
    env vars on the subprocess so the resolved path lands inside a
    per-test tmp directory, keeping the user's real
    ``~/AppData/Local/Sethlans`` (or equivalent) untouched.
    """
    worker_data_dir = Path(worker_data_dir)
    worker_data_dir.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    if system == "Windows":
        # get_data_dir() returns LOCALAPPDATA/Sethlans/worker, so we
        # point LOCALAPPDATA at the tmp parent and ``Sethlans/worker``
        # gets auto-created underneath on first write.
        env["LOCALAPPDATA"] = str(worker_data_dir)
    elif system == "Darwin":
        # get_data_dir() returns HOME/Library/Application Support/...
        # Overriding HOME is heavy-handed but it's the only hook the
        # paths.py module exposes on macOS.
        env["HOME"] = str(worker_data_dir)
    else:
        # Linux / other POSIX — XDG_DATA_HOME is the documented override.
        env["XDG_DATA_HOME"] = str(worker_data_dir)


def build_worker_env(
    enrollment_key, manager_host, manager_port, worker_data_dir=None,
):
    """
    Build the environment dict for the worker subprocess.

    Uses the SETHLANS_{SECTION}_{KEY} naming convention from
    worker config.py. The wizard's unattended path (FR-32) reads
    ``SETHLANS_WORKER_ENROLLMENT_KEY``, NOT the old
    ``SETHLANS_MANAGER_ENROLLMENT_KEY`` — the manager-namespaced name
    no longer exists in the codebase.

    ``worker_data_dir`` (optional): a per-test path used to redirect
    the worker's JSON config store away from the user's real data dir.
    If omitted, the worker will write to the user's real data dir
    which pollutes it across test runs — callers should always pass
    an explicit path from the test's tmp fixture.
    """
    env = os.environ.copy()
    env.update({
        "SETHLANS_MANAGER_HOST": manager_host,
        "SETHLANS_MANAGER_PORT": str(manager_port),
        # FR-32: unattended-wizard env var. The manager-prefixed
        # variant from the legacy heartbeat-enrollment flow is gone.
        "SETHLANS_WORKER_ENROLLMENT_KEY": enrollment_key,
        "SETHLANS_WORKER_HEARTBEAT_INTERVAL": "5",
        "SETHLANS_WORKER_POLLING_INTERVAL": "3",
        "SETHLANS_WORKER_UI_ENABLED": "false",
        # Disable idle detection so the worker claims jobs immediately
        # regardless of host keyboard/mouse activity (e.g., active CI
        # runners on artist workstations like macOS Apple Silicon).
        "SETHLANS_IDLE_DETECTION_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    # FR-28 exception list: SETHLANS_MANAGER_API_TOKEN,
    # SETHLANS_MANAGER_CERT_FINGERPRINT, and SETHLANS_MANAGER_MANAGER_ID
    # are IGNORED by config.py (logged + dropped). Setting them to ""
    # as the old code did accomplishes nothing — the wizard-populated
    # JSON config store is authoritative. Instead of setting them, we
    # isolate the JSON config store via the per-test data dir below.
    if worker_data_dir is not None:
        _apply_worker_data_dir(env, worker_data_dir)
    return env


def generate_secrets():
    """Generate per-test-class secrets for enrollment and Django.

    The enrollment key is a 16-character Crockford base32 string (see
    ``workers.enrollment_key.generate_key``), which is the only format
    the manager's ``normalize()`` accepts per FR-15.
    """
    return {
        "enrollment_key": _generate_crockford_key(),
        "secret_key": _py_secrets.token_urlsafe(50),
    }
