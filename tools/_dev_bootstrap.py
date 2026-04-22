# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Small helpers used by ``tools/sethlans.sh`` and ``tools/sethlans.ps1``.

Post-Waitress-migration, the dev scripts need to:

* read the current enrollment key out of the ``ManagerSettings`` DB
  row (the legacy ``manager.ini [security] enrollment_key`` is gone);
* render the manager Caddyfile using the shared template so Caddy can
  terminate TLS on port 8080 in front of the two Waitress listeners.

Both operations need Django set up and access to the
``sethlans_manager`` / ``workers`` packages, so they live here rather
than as inline ``python -c`` blobs in the shell scripts.

Usage:
    python tools/_dev_bootstrap.py enrollment-key
    python tools/_dev_bootstrap.py render-caddyfile \\
        --manager-dir /abs/path/to/manager \\
        --public-tls-port 8080 --loopback-plaintext-port 8089 \\
        --waitress-public-port 8090 --waitress-internal-port 8088
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _bootstrap_django(manager_dir: Path) -> None:
    """Put ``manager/`` and project root on ``sys.path`` and set up Django.

    ``sethlans_manager.settings`` imports ``shared.frozen_paths``, so
    the project root (parent of ``manager/``) also has to be on the
    path — mirrors ``manager/run_manager.py`` bootstrapping.
    """
    project_root = manager_dir.parent
    for p in (str(project_root), str(manager_dir)):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "sethlans_manager.settings",
    )
    import django
    django.setup()


def cmd_enrollment_key(args: argparse.Namespace) -> int:
    """Print the canonical enrollment key from the DB to stdout."""
    _bootstrap_django(Path(args.manager_dir).resolve())
    from workers import enrollment_key
    sys.stdout.write(enrollment_key.load_current())
    sys.stdout.flush()
    return 0


def cmd_generate_config(args: argparse.Namespace) -> int:
    """Idempotently ensure ``manager.ini`` has server port + secret key.

    Dropped inline into ``sethlans.sh`` previously; moved here so the
    path arrives as an argv value. MSYS/Git-Bash translates ``/c/...``
    argv paths to Windows form before the exe sees them; string
    literals inside a ``python -c`` block get no such translation,
    which is why the inline version blew up on first-boot under Git
    Bash.

    No Django bootstrap needed — stdlib only.
    """
    import configparser
    import secrets

    manager_dir = Path(args.manager_dir).resolve()
    config_path = manager_dir / "manager.ini"

    config = configparser.ConfigParser()
    if config_path.exists():
        config.read(config_path)
        print("[OK] Found existing manager.ini")
    else:
        print("[NEW] Creating manager.ini")

    for section in ("server", "security"):
        if not config.has_section(section):
            config.add_section(section)
    if not config.has_option("server", "port"):
        config.set("server", "port", str(args.port))
    if not config.get("security", "secret_key", fallback=""):
        config.set(
            "security", "secret_key", secrets.token_urlsafe(50),
        )
        print("[OK] Generated SECRET_KEY")
    if not config.get("security", "debug", fallback=""):
        config.set("security", "debug", "true")
    # Strip legacy [security] enrollment_key — it now lives in the
    # ManagerSettings DB row (migration 0017).
    if config.has_option("security", "enrollment_key"):
        config.remove_option("security", "enrollment_key")
        print("[OK] Removed legacy enrollment_key from manager.ini")

    with open(config_path, "w", encoding="utf-8") as f:
        config.write(f)
    return 0


def cmd_render_caddyfile(args: argparse.Namespace) -> int:
    """Render the manager Caddyfile and write it to ``<manager_dir>/caddy/Caddyfile``.

    Uses the manager dir as both ``manager_data_dir`` (the containment
    root the template validates cert/key paths against) and the TLS
    directory parent. This matches source-mode defaults where
    ``tls_setup.setup_certificates`` drops certs at
    ``<manager_dir>/tls/``.
    """
    manager_dir = Path(args.manager_dir).resolve()
    _bootstrap_django(manager_dir)
    from sethlans_manager.caddy_template import render_manager_caddyfile

    cert_path = manager_dir / "tls" / "cert.pem"
    key_path = manager_dir / "tls" / "key.pem"
    caddy_dir = manager_dir / "caddy"
    caddy_dir.mkdir(parents=True, exist_ok=True)
    caddyfile_path = caddy_dir / "Caddyfile"

    rendered = render_manager_caddyfile(
        public_tls_port=args.public_tls_port,
        loopback_plaintext_port=args.loopback_plaintext_port,
        cert_path=cert_path,
        key_path=key_path,
        manager_data_dir=manager_dir,
        waitress_public_port=args.waitress_public_port,
        waitress_internal_port=args.waitress_internal_port,
    )
    caddyfile_path.write_text(rendered, encoding="utf-8")
    sys.stdout.write(str(caddyfile_path))
    sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dev-script bootstrap helpers (enrollment + Caddy).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_enroll = sub.add_parser(
        "enrollment-key",
        help="Print canonical enrollment key from the DB.",
    )
    p_enroll.add_argument("--manager-dir", required=True)
    p_enroll.set_defaults(func=cmd_enrollment_key)

    p_config = sub.add_parser(
        "generate-config",
        help="Create/update manager.ini with secret_key + port.",
    )
    p_config.add_argument("--manager-dir", required=True)
    p_config.add_argument("--port", type=int, default=8080)
    p_config.set_defaults(func=cmd_generate_config)

    p_caddy = sub.add_parser(
        "render-caddyfile", help="Render manager Caddyfile.",
    )
    p_caddy.add_argument("--manager-dir", required=True)
    p_caddy.add_argument("--public-tls-port", type=int, default=8080)
    p_caddy.add_argument(
        "--loopback-plaintext-port", type=int, default=8089,
    )
    p_caddy.add_argument("--waitress-public-port", type=int, default=8090)
    p_caddy.add_argument(
        "--waitress-internal-port", type=int, default=8088,
    )
    p_caddy.set_defaults(func=cmd_render_caddyfile)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
