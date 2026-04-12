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
import ssl
import subprocess
import sys
from pathlib import Path

# Ensure the manager directory is on sys.path so that
# sethlans_manager package imports work regardless of cwd.
MANAGER_DIR = Path(__file__).resolve().parent
if str(MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(MANAGER_DIR))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sethlans_manager.settings')

import uvicorn  # noqa: E402

from sethlans_manager.cert_utils import (  # noqa: E402
    CertificateError,
    check_cert_expiry_warning,
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)
from sethlans_manager.runtime_init import (  # noqa: E402
    get_enrollment_config,
    initialize_runtime_state,
)

logger = logging.getLogger(__name__)

MANAGE_PY = str(MANAGER_DIR / 'manage.py')
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
        config_file_path = MANAGER_DIR / 'manager.ini'
        if config_file_path.exists():
            config.read(config_file_path)
            if not host and config.has_option('server', 'host'):
                host = config.get('server', 'host')
            if not port and config.has_option('server', 'port'):
                port = config.get('server', 'port')
    except Exception as e:
        print(f"[WARNING] Could not read manager.ini: {e}", file=sys.stderr)

    return (host or '0.0.0.0', port or '8080')


def get_tls_config():
    """Read BYO cert/key paths from env vars or manager.ini [tls] section.

    Returns (cert_file, key_file) — both None if not configured.
    """
    cert_file = os.getenv('SETHLANS_TLS_CERT_FILE')
    key_file = os.getenv('SETHLANS_TLS_KEY_FILE')

    if cert_file and key_file:
        return (cert_file, key_file)

    try:
        config = configparser.ConfigParser()
        config_file_path = MANAGER_DIR / 'manager.ini'
        if config_file_path.exists():
            config.read(config_file_path)
            if not cert_file and config.has_option('tls', 'cert_file'):
                cert_file = config.get('tls', 'cert_file') or None
            if not key_file and config.has_option('tls', 'key_file'):
                key_file = config.get('tls', 'key_file') or None
    except Exception as e:
        print(f"[WARNING] Could not read TLS config: {e}", file=sys.stderr)

    if bool(cert_file) != bool(key_file):
        configured = 'cert_file' if cert_file else 'key_file'
        missing = 'key_file' if cert_file else 'cert_file'
        print(
            f"[WARNING] TLS {configured} is set but {missing} is not. "
            f"Both must be provided for a custom certificate. "
            f"Falling back to self-signed cert.",
            file=sys.stderr,
        )
        return (None, None)

    return (cert_file, key_file)


def get_tls_dir(dev_mode):
    """Return the TLS directory path based on mode.

    Dev mode:  <project_root>/temp/dev-tls/
    Prod mode: <manager_dir>/tls/
    """
    if dev_mode:
        return PROJECT_ROOT / 'temp' / 'dev-tls'
    return MANAGER_DIR / 'tls'


def setup_certificates(dev_mode):
    """Generate or load TLS certificates. Returns (cert_path, key_path, cert).

    For BYO certs, validates and returns the user-provided paths.
    For auto-generated certs, creates them if they don't exist.
    Logs the fingerprint only on first generation.
    """
    byo_cert_file, byo_key_file = get_tls_config()

    if byo_cert_file and byo_key_file:
        # BYO certificate — validate it
        cert_path = Path(byo_cert_file)
        key_path = Path(byo_key_file)
        cert = load_and_validate_cert(cert_path, key_path)
        return (cert_path, key_path, cert)

    # Self-signed cert flow
    tls_dir = get_tls_dir(dev_mode)
    cert_path = tls_dir / 'cert.pem'
    key_path = tls_dir / 'key.pem'

    first_generation = not cert_path.exists()

    if first_generation:
        generate_self_signed_cert(cert_path, key_path)

    cert = load_and_validate_cert(cert_path, key_path)

    if first_generation:
        fingerprint = get_cert_fingerprint(cert)
        logger.info("TLS cert fingerprint: %s", fingerprint)

    return (cert_path, key_path, cert)


def build_ssl_context(cert_path, key_path):
    """Build an SSLContext with TLS 1.2 minimum version."""
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ssl_ctx.load_cert_chain(str(cert_path), str(key_path))
    return ssl_ctx


def run_migrations():
    """Apply pending database migrations."""
    print("--- Applying database migrations... ---")
    try:
        subprocess.run(
            [sys.executable, MANAGE_PY, "migrate"],
            check=True,
        )
        print("--- Migrations applied successfully. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[ERROR] Database migration failed. Error: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


def run_collectstatic():
    """Collect static files for production serving."""
    print("--- Collecting static files... ---")
    try:
        subprocess.run(
            [sys.executable, MANAGE_PY, "collectstatic", "--noinput"],
            check=True,
        )
        print("--- Static files collected. ---")
    except subprocess.CalledProcessError as e:
        print(
            f"\n[WARNING] collectstatic failed: {e}",
            file=sys.stderr,
        )


def _prepare_runtime(cert, host, port):
    """Initialise Django and populate ``runtime_state``."""
    try:
        import django  # noqa: F401
        django.setup()
    except Exception as exc:  # pragma: no cover - hard failure
        print(f"\n[ERROR] Django setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
    enrollment_cfg = get_enrollment_config(MANAGER_DIR)
    initialize_runtime_state(cert, enrollment_cfg, host, port)


def _launch_uvicorn(host, port, cert_path, key_path, dev_mode):
    """Hand control to uvicorn with the correct reload/production mode.

    uvicorn's ``run()`` does not accept a pre-built ``SSLContext``;
    it wants ``ssl_keyfile`` / ``ssl_certfile`` paths (it builds the
    context itself so it can be rebuilt on reload).  We still keep
    :func:`build_ssl_context` around for any downstream tooling that
    wants the pre-validated context object.
    """
    ssl_common = {
        "ssl_keyfile": str(key_path),
        "ssl_certfile": str(cert_path),
        "ssl_version": ssl.PROTOCOL_TLS_SERVER,
    }
    if dev_mode:
        uvicorn.run(
            "sethlans_manager.asgi:application",
            host=host,
            port=int(port),
            reload=True,
            reload_dirs=[str(MANAGER_DIR)],
            **ssl_common,
        )
    else:
        uvicorn.run(
            "sethlans_manager.asgi:application",
            host=host,
            port=int(port),
            **ssl_common,
        )


def main():
    """Entry point for ``python manager/run_manager.py``."""
    dev_mode = '--dev' in sys.argv

    # Signal the ASGI lifespan hooks (asgi.py) that we are in --dev mode
    # so that the broadcaster does not start in the reloader parent
    # process.  Must be set BEFORE uvicorn.run() to propagate to workers.
    if dev_mode:
        os.environ['SETHLANS_DEV_MODE'] = '1'

    try:
        cert_path, key_path, cert = setup_certificates(dev_mode)
    except CertificateError as e:
        print(f"\n[ERROR] TLS certificate error: {e}", file=sys.stderr)
        sys.exit(1)

    check_cert_expiry_warning(cert)
    run_migrations()

    if not dev_mode:
        run_collectstatic()

    # Validate the cert/key pair by instantiating an SSLContext
    # (surfaces mismatched/bad files before uvicorn boots).
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
