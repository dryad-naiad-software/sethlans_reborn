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
    python manager/run_manager.py                  # Production mode
    python manager/run_manager.py --dev            # Dev hot-reload
    python manager/run_manager.py --manage <cmd>   # Run a Django mgmt
                                                   # subcommand and exit
    python manager/run_manager.py --setup          # Setup mode (exit 10 saved)
    python manager/run_manager.py --hash-password  # Password on stdin -> hash

Phase 6 replaced the ``--dev`` fail-fast stub with a watchdog-based
parent/child wrapper. The parent process owns the filesystem observer
and signals the child on any ``*.py`` change under ``manager/`` or
``shared/``; the child is this script again without ``--dev``, so it
runs the production boot (migrate, collectstatic, Waitress).

Issue #191 added ``--manage <subcommand> [args...]``. In frozen
PyInstaller mode the launcher cannot invoke
``[sys.executable, manage.py, ...]`` because ``sys.executable`` is the
launcher exe. ``--manage`` routes the subcommand through the bundled
``run_manager`` binary instead. The subcommand is constrained to the
allowlist in ``sethlans_manager.manage_dispatch`` so that the frozen
binary never exposes commands like ``createsuperuser`` or ``shell``
to local users. ``--manage`` is mutually exclusive with ``--dev``.
"""
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
    get_manager_dir,
    is_frozen,
)

if is_frozen():
    MANAGER_DIR = get_manager_dir()


def _dispatch_setup_mode(argv) -> None:
    """``--setup`` / ``--hash-password``: run before any run-time import.

    Setup mode loads ``settings_setup`` (no database, no ``workers``
    app), so it must start before the imports below pull in the running
    manager's modules. Never returns when one of the two flags is given.
    """
    if '--setup' not in argv and '--hash-password' not in argv:
        return
    from sethlans_manager.setup_mode import main as setup_main
    sys.exit(setup_main(argv))


_dispatch_setup_mode(sys.argv[1:])

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings')

from sethlans_manager.bind_config import (  # noqa: E402
    get_manager_bind as _resolve_manager_bind,
)
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
# The dev watchdog's child runs this script again, without ``--dev``.
RUN_MANAGER_PY = Path(__file__).resolve()

if is_frozen():
    PROJECT_ROOT = get_app_dir()
else:
    PROJECT_ROOT = MANAGER_DIR.parent


def get_manager_bind():
    """Return (host, port) tuple respecting the override hierarchy.

    1. Environment variables (SETHLANS_MANAGER_HOST, SETHLANS_MANAGER_PORT)
    2. The settings file ``[server]`` (``sethlans.ini``)
    3. Hardcoded defaults (0.0.0.0, 8080)

    See :func:`sethlans_manager.bind_config.get_manager_bind`, the one
    resolver the local worker's enrollment and the summary use too.
    """
    return _resolve_manager_bind(get_config_file_path())


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
      3. ``[server] waitress_loopback_port_internal`` (settings file).
      4. ``[server] loopback_port`` (legacy).
      5. Default ``8088``.
    """
    ini_path = get_config_file_path()
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
    enrollment_cfg = get_enrollment_config(get_config_file_path())
    cleanup_stale_setup_section(get_config_file_path())
    initialize_runtime_state(cert, enrollment_cfg, host, port)


def _frozen_boot_sequence(dev_mode):
    """Frozen-mode boot: django.setup() before in-process migrations.

    In frozen mode, subprocess-based migrations fail because
    ``sys.executable`` is the frozen binary, not a Python interpreter.
    Boot order: django.setup() -> migrate -> collectstatic.

    Delegates the ``django.setup()`` + logging-configure prologue to
    :func:`sethlans_manager.manage_dispatch.setup_django_for_management`
    so the runtime path and the ``--manage`` dispatch path share a
    single Django-init implementation (#191).
    """
    from sethlans_manager.manage_dispatch import (
        setup_django_for_management,
    )
    from sethlans_manager.migration_runner import (
        run_collectstatic_inprocess,
        run_migrations_inprocess,
    )

    setup_django_for_management()

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
    # The child is run_manager.py itself, not manage.py: a bare
    # manage.py prints Django's help and exits (issue #293).
    rc = run_dev_watchdog(RUN_MANAGER_PY, sys.argv[1:])
    sys.exit(rc)


def main():
    """Entry point for ``python manager/run_manager.py``."""
    if '--manage' in sys.argv:
        # Mutual exclusion + missing-subcommand + allowlist checks all
        # happen inside dispatch_manage_mode BEFORE django.setup runs
        # (#191 / spec FR-MGMT2 + FR-MGMT3). Never returns.
        from sethlans_manager.manage_dispatch import dispatch_manage_mode
        dispatch_manage_mode()

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

    ini_path = get_config_file_path()
    public_port = get_waitress_public_port(ini_path)
    internal_port = get_waitress_internal_port(ini_path)

    # SETHLANS_DEV_MODE=1 is set only by the --dev watchdog parent.
    mode = "DEV" if os.environ.get("SETHLANS_DEV_MODE") == "1" else "PRODUCTION"
    print(
        f"\n--- Starting Sethlans Manager ({mode}) "
        f"-- public vhost {host}:{port} via Caddy -> "
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
