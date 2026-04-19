# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup-time population of ``sethlans_manager.runtime_state``.

Extracted from ``run_manager.py`` so that module stays under the 300-line
limit.  The helpers here run AFTER Django has been set up but BEFORE
uvicorn takes over — the ASGI lifespan hooks in ``asgi.py`` read these
values on process startup.
"""

import configparser
import logging
import os
import socket
import sys
import typing
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def get_enrollment_config(manager_dir: Path) -> dict:
    """Read ``[enrollment]`` section from ``manager.ini``.

    Returns a dict with keys ``name`` and ``advertised_ip``, either of
    which may be absent.  Env vars take precedence.
    """
    result = {
        "name": os.getenv("SETHLANS_ENROLLMENT_NAME"),
        "advertised_ip": os.getenv(
            "SETHLANS_ENROLLMENT_ADVERTISED_IP",
        ),
    }
    try:
        config = configparser.ConfigParser()
        config_file_path = manager_dir / "manager.ini"
        if config_file_path.exists():
            config.read(config_file_path)
            if result["name"] is None and config.has_option(
                "enrollment", "name",
            ):
                result["name"] = config.get("enrollment", "name")
            if result["advertised_ip"] is None and config.has_option(
                "enrollment", "advertised_ip",
            ):
                result["advertised_ip"] = config.get(
                    "enrollment", "advertised_ip",
                )
    except Exception as exc:
        print(
            f"[WARNING] Could not read [enrollment] section: {exc}",
            file=sys.stderr,
        )
    return result


def detect_primary_ipv4() -> typing.Optional[str]:
    """Return the first non-loopback IPv4 from an enumeration of NICs.

    Skips ``docker``/``br-``/``veth``/``vEthernet`` virtual bridges.
    Returns ``None`` when nothing suitable is available — the
    broadcaster thread will log an ERROR and exit cleanly in that case.
    """
    try:
        import psutil
    except ImportError:
        return None

    skip_prefixes = ("docker", "br-", "veth", "vEthernet")
    for iface, addrs in psutil.net_if_addrs().items():
        if any(iface.startswith(p) for p in skip_prefixes):
            continue
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            ip = addr.address
            if ip and not ip.startswith("127."):
                return ip
    return None


def apply_enrollment_key_env_override() -> None:
    """Honour ``SETHLANS_SECURITY_ENROLLMENT_KEY`` by writing to the DB.

    Must be called after ``django.setup()``.  If the env var is set,
    its value is normalised and persisted into the ``ManagerSettings``
    singleton row; an invalid value aborts startup.
    """
    env_key = os.getenv("SETHLANS_SECURITY_ENROLLMENT_KEY")
    if not env_key:
        return
    from workers import enrollment_key as ek
    from workers.models import ManagerSettings

    try:
        normalized = ek.normalize(env_key)
    except ValueError as exc:
        print(
            f"\n[ERROR] SETHLANS_SECURITY_ENROLLMENT_KEY invalid: "
            f"{exc}", file=sys.stderr,
        )
        sys.exit(1)

    row, _ = ManagerSettings.objects.get_or_create(pk=1)
    row.enrollment_key = normalized
    row.save(
        update_fields=[
            "enrollment_key", "enrollment_key_updated_at",
        ],
    )
    logger.warning(
        "Enrollment key overridden from "
        "SETHLANS_SECURITY_ENROLLMENT_KEY environment variable.",
    )


def initialize_runtime_state(cert, enrollment_cfg, bind_host, bind_port):
    """Populate ``sethlans_manager.runtime_state`` from DB + env.

    Called after migrations have run and the TLS cert is loaded.  Any
    failure here is fatal — we cannot run a useful server without these
    values.
    """
    from sethlans_manager import __version__ as manager_version
    from sethlans_manager import runtime_state
    from sethlans_manager.cert_utils import get_cert_fingerprint
    from workers.models import ManagerSettings

    apply_enrollment_key_env_override()

    try:
        settings_row = ManagerSettings.objects.get(pk=1)
    except ManagerSettings.DoesNotExist:
        print(
            "\n[ERROR] ManagerSettings row missing — migrations may "
            "not have run.", file=sys.stderr,
        )
        sys.exit(1)

    runtime_state.manager_id = settings_row.manager_id
    # Fresh UUID per process start — used by GET /api/health/ so the
    # setup-wizard restart poll can detect a completed restart (FR-14c).
    runtime_state.manager_boot_id = uuid.uuid4().hex
    runtime_state.cert_fingerprint = get_cert_fingerprint(cert)
    runtime_state.broadcaster_name = (
        enrollment_cfg.get("name") or "Sethlans Manager"
    )
    runtime_state.broadcaster_host = socket.gethostname()
    runtime_state.broadcaster_ip = (
        enrollment_cfg.get("advertised_ip")
        or detect_primary_ipv4()
        or bind_host
    )
    runtime_state.broadcaster_port = int(bind_port)
    runtime_state.broadcaster_version = manager_version
