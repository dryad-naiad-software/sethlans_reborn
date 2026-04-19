# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Entry point for the Sethlans Manager HTTPS server.

Applies database migrations, generates or loads TLS certificates,
and launches uvicorn with a custom SSLContext (TLS 1.2+).

Usage:
    python manager/run_manager.py          # Production mode
    python manager/run_manager.py --dev    # Dev mode (hot reload, dev TLS dir)
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
    build_ssl_context,
    get_config_file_path,
    setup_certificates,
)
from sethlans_manager.uvicorn_launcher import launch as _launch_servers  # noqa: E402

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
    """Return the loopback listener port.

    Override hierarchy (tray-helper-unified FR-22 / FR-22b):
      1. ``SETHLANS_MANAGER_LOOPBACK_PORT`` env var.
      2. ``manager.ini [server] loopback_port``.
      3. Default ``8088``.
    """
    port = os.getenv('SETHLANS_MANAGER_LOOPBACK_PORT')
    try:
        config = configparser.ConfigParser()
        config_file_path = get_config_file_path(MANAGER_DIR)
        if config_file_path.exists():
            config.read(config_file_path)
            if not port and config.has_option('server', 'loopback_port'):
                port = config.get('server', 'loopback_port')
    except Exception as e:
        print(
            f"[WARNING] Could not read manager.ini: {e}",
            file=sys.stderr,
        )
    return port or '8088'


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


def _launch_uvicorn(host, port, cert_path, key_path, dev_mode):
    """Thin shim around :func:`sethlans_manager.uvicorn_launcher.launch`.

    Preserved as a module-level entry point for tests and for backwards
    compat with scripts that import it directly.
    """
    _launch_servers(
        host=host,
        port=port,
        cert_path=cert_path,
        key_path=key_path,
        dev_mode=dev_mode,
        manager_dir=MANAGER_DIR,
        get_loopback_port=get_loopback_port,
    )


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


def main():
    """Entry point for ``python manager/run_manager.py``."""
    dev_mode = '--dev' in sys.argv

    # Signal the ASGI lifespan hooks (asgi.py) that we are in --dev mode
    # so that the broadcaster does not start in the reloader parent
    # process.  Must be set BEFORE uvicorn.run() to propagate to workers.
    if dev_mode:
        os.environ['SETHLANS_DEV_MODE'] = '1'

    try:
        cert_path, key_path, cert = setup_certificates(
            dev_mode, MANAGER_DIR, PROJECT_ROOT,
        )
    except CertificateError as e:
        print(f"\n[ERROR] TLS certificate error: {e}", file=sys.stderr)
        sys.exit(1)

    check_cert_expiry_warning(cert)

    if is_frozen():
        _frozen_boot_sequence(dev_mode)
    else:
        from sethlans_manager.migration_runner import (
            run_collectstatic_subprocess,
            run_migrations_subprocess,
        )
        run_migrations_subprocess(MANAGE_PY)
        if not dev_mode:
            run_collectstatic_subprocess(MANAGE_PY)

    build_ssl_context(cert_path, key_path)
    host, port = get_manager_bind()

    _prepare_runtime(cert, host, port)

    mode_label = "DEV" if dev_mode else "PRODUCTION"
    print(f"\n--- Starting Sethlans Manager ({mode_label}) "
          f"on https://{host}:{port} ---")
    print("--- To stop the server, press CTRL+C ---")

    try:
        _launch_uvicorn(host, port, cert_path, key_path, dev_mode)
    except KeyboardInterrupt:
        print("\n--- Sethlans Manager stopped ---")


if __name__ == "__main__":
    main()
