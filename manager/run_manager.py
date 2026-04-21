# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Entry point for the Sethlans Manager HTTP(S) server.

Applies database migrations and generates or loads TLS certificates
(Caddy consumes them at the front door — Waitress itself runs plaintext
on loopback). Phase 5 of the waitress-migration spec swapped the
serving path to Waitress+WSGI with two loopback listeners
(public-origin + internal-origin), both fronted by Caddy.

Usage:
    python manager/run_manager.py          # Production mode (Waitress)
    python manager/run_manager.py --dev    # Dev hot-reload (watchdog).

Phase 6 replaced the ``--dev`` fail-fast stub with a watchdog-based
parent/child wrapper. The parent process owns the filesystem observer
and signals the child on any ``*.py`` change under ``manager/``; the
child process runs the production Waitress path.
"""
import configparser
import logging
import os
import sys
from pathlib import Path

# Ensure the manager directory is on sys.path so that
# sethlans_manager package imports work regardless of cwd.
MANAGER_DIR = Path(__file__).resolve().parent
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

# Ensure shared/ is on sys.path for frozen_paths imports.
_SHARED_DIR = str(MANAGER_DIR.parent)
if _SHARED_DIR not in sys.path:
    sys.path.insert(0, _SHARED_DIR)

from shared.frozen_paths import (  # noqa: E402
    get_app_dir,
    get_data_dir,
    get_manager_dir,
    is_frozen,
)

if is_frozen():
    MANAGER_DIR = get_manager_dir()

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings')

from sethlans_manager.cert_utils import (  # noqa: E402
    CertificateError,
    check_cert_expiry_warning,
)
from sethlans_manager.runtime_init import (  # noqa: E402
    get_enrollment_config,
    cleanup_stale_setup_section,
    initialize_runtime_state,
)
from sethlans_manager.tls_setup import (  # noqa: E402
    get_config_file_path,
    setup_certificates,
)
from sethlans_manager.waitress_config import (  # noqa: E402
    get_waitress_internal_port,
    get_waitress_public_port,
)
from sethlans_manager.waitress_launcher import (  # noqa: E402
    launch as _launch_waitress,
    stop_waitress_listeners,
)

logger = logging.getLogger(__name__)

MANAGE_PY = str(MANAGER_DIR / 'manage.py')

if is_frozen():
    PROJECT_ROOT = get_app_dir()
else:
    PROJECT_ROOT = MANAGER_DIR.parent


def get_manager_bind():
    """Return (host, port) tuple respecting the override hierarchy.

    1. Environment variables (SETHLANS_MANAGER_HOST, SETHLANS_MANAGER_PORT)
    2. manager.ini file
    3. Hardcoded defaults (0.0.0.0, 8080)
    """
    host = os.getenv('SETHLANS_MANAGER_HOST')
    port = os.getenv('SETHLANS_MANAGER_PORT')

    try:
        config = configparser.ConfigParser()
        config_file_path = get_config_file_path(MANAGER_DIR)
        if config_file_path.exists():
            config.read(config_file_path)
            if not host and config.has_option('server', 'host'):
                host = config.get('server', 'host')
            if not port and config.has_option('server', 'port'):
                port = config.get('server', 'port')
    except Exception as e:
        print(f"[WARNING] Could not read manager.ini: {e}", file=sys.stderr)

    return (host or '0.0.0.0', port or '8080')


def get_loopback_port():
    """Return the internal-origin Waitress listener port.

    Thin wrapper around
    :func:`sethlans_manager.waitress_config.get_waitress_internal_port`
    that preserves the pre-Phase-5 string return type for callers that
    expect it (e.g. the Phase 2 loopback-port unit tests).

    Override hierarchy (tray-helper-unified FR-22 / FR-22b +
    waitress-migration Phase 5):
      1. ``SETHLANS_MANAGER_LOOPBACK_PORT`` env var (legacy).
      2. ``SETHLANS_MANAGER_WAITRESS_PORT_INTERNAL`` env var.
      3. ``manager.ini [server] waitress_loopback_port_internal``.
      4. ``manager.ini [server] loopback_port`` (legacy).
      5. Default ``8088``.
    """
    ini_path = get_config_file_path(MANAGER_DIR)
    return str(get_waitress_internal_port(ini_path))


def _prepare_runtime(cert, host, port):
    """Initialise Django and populate ``runtime_state``.

    In frozen mode, ``django.setup()`` has already been called by
    ``_frozen_boot_sequence()``.
    """
    if not is_frozen():
        try:
            import django
            django.setup()
        except Exception as exc:  # pragma: no cover
            print(
                f"\n[ERROR] Django setup failed: {exc}", file=sys.stderr,
            )
            sys.exit(1)
        from sethlans_manager.logging_config import configure as _cfg
        _cfg()
    config_dir = (
        get_data_dir('manager') if is_frozen() else MANAGER_DIR
    )
    enrollment_cfg = get_enrollment_config(config_dir)
    cleanup_stale_setup_section(config_dir)
    initialize_runtime_state(cert, enrollment_cfg, host, port)


def _frozen_boot_sequence(dev_mode):
    """Frozen-mode boot: django.setup() before in-process migrations.

    In frozen mode, subprocess-based migrations fail because
    ``sys.executable`` is the frozen binary, not a Python interpreter.
    Boot order: django.setup() -> migrate -> collectstatic.
    """
    import django

    from sethlans_manager.migration_runner import (
        run_collectstatic_inprocess,
        run_migrations_inprocess,
    )

    try:
        django.setup()
    except Exception as exc:
        print(f"\n[ERROR] Django setup failed: {exc}", file=sys.stderr)
        sys.exit(1)

    from sethlans_manager.logging_config import configure as _cfg
    _cfg()

    run_migrations_inprocess()
    if not dev_mode:
        run_collectstatic_inprocess()


def _dispatch_dev_mode() -> None:
    """Handle ``--dev``: parent→watchdog wrapper, child→fall through.

    Called only when ``--dev`` is present in ``sys.argv``. If the
    ``SETHLANS_DEV_IS_PARENT`` env marker is absent we are the parent
    and hand off to ``run_dev_watchdog``; ``sys.exit`` with its rc so
    we never return to production boot. If the marker is present we
    are a child that somehow still sees ``--dev`` (pathological — the
    parent strips it from child argv). Log a warning and fall through
    to the production path.

    ``watchdog`` is a dev-only dependency; ImportError surfaces as a
    clean exit-2 with a pointer at ``requirements-dev.txt`` rather
    than a raw traceback.
    """
    if os.environ.get("SETHLANS_DEV_IS_PARENT") == "1":
        print(
            "[WARN] --dev seen in child argv; expected it to be "
            "stripped by the parent. Falling through to production "
            "path.",
            file=sys.stderr,
        )
        return
    try:
        from sethlans_manager.dev_watchdog import run_dev_watchdog
    except ImportError:
        print(
            "[ERROR] watchdog is a dev-only dependency; install via "
            "`pip install -r requirements-dev.txt` before using --dev",
            file=sys.stderr,
        )
        sys.exit(2)
    rc = run_dev_watchdog(Path(MANAGE_PY), sys.argv[1:])
    sys.exit(rc)


def main():
    """Entry point for ``python manager/run_manager.py``."""
    if '--dev' in sys.argv:
        _dispatch_dev_mode()

    try:
        cert_path, key_path, cert = setup_certificates(
            dev_mode=False,
            manager_dir=MANAGER_DIR,
            project_root=PROJECT_ROOT,
        )
    except CertificateError as e:
        print(f"\n[ERROR] TLS certificate error: {e}", file=sys.stderr)
        sys.exit(1)

    check_cert_expiry_warning(cert)

    if is_frozen():
        _frozen_boot_sequence(dev_mode=False)
    else:
        from sethlans_manager.migration_runner import (
            run_collectstatic_subprocess,
            run_migrations_subprocess,
        )
        run_migrations_subprocess(MANAGE_PY)
        run_collectstatic_subprocess(MANAGE_PY)

    host, port = get_manager_bind()
    _prepare_runtime(cert, host, port)

    ini_path = get_config_file_path(MANAGER_DIR)
    public_port = get_waitress_public_port(ini_path)
    internal_port = get_waitress_internal_port(ini_path)

    print(
        "\n--- Starting Sethlans Manager (PRODUCTION) "
        f"— public vhost {host}:{port} via Caddy → "
        f"Waitress 127.0.0.1:{public_port} ---",
    )
    print("--- To stop the server, press CTRL+C ---")

    try:
        _launch_waitress(public_port, internal_port, ini_path)
    except KeyboardInterrupt:
        stop_waitress_listeners()
        print("\n--- Sethlans Manager stopped ---")


if __name__ == "__main__":
    main()
