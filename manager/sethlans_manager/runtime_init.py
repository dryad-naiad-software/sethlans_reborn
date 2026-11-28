# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup-time population of ``sethlans_manager.runtime_state``.

Extracted from ``run_manager.py`` so that module stays under the 300-line
limit.  The helpers here run AFTER Django has been set up but BEFORE
Waitress takes over — the launcher's broadcaster supervisor reads
these values from ``broadcaster_params.json`` on process startup.
"""

import logging
import os
import socket
import sys
import typing
import uuid
from pathlib import Path
from typing import Optional

from shared.settings_file import read_settings

logger = logging.getLogger(__name__)

# Set to "0" by launcher/container_entrypoint.py; see
# _publish_broadcaster_params.
PUBLISH_BROADCASTER_PARAMS_ENV = "SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS"


def get_enrollment_config(ini_path: Optional[Path] = None) -> dict:
    """Read the ``[enrollment]`` section of the settings file.

    *ini_path* defaults to :func:`shared.settings_file.settings_path`.
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
        config = read_settings(ini_path)
        for key in ("name", "advertised_ip"):
            if result[key] is None and config.has_option("enrollment", key):
                result[key] = config.get("enrollment", key)
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

    _publish_broadcaster_params()


def _publish_broadcaster_params() -> None:
    """Atomically write broadcaster params to ``<data_dir>/broadcaster_params.json``.

    Manager spec Phase 3: the launcher owns the ``MulticastBroadcaster``
    lifecycle, but the inputs (manager_id, broadcaster_ip, etc.) are
    computed Django-side. This function publishes those inputs so the
    launcher's poll loop can pick them up and start the broadcaster.

    This file-based IPC is how the launcher obtains the inputs —
    Django no longer hosts the broadcaster in-process (relocated in
    Phase 3 and formalised with the Phase 7 deletions).

    Skipped when ``SETHLANS_MANAGER_PUBLISH_BROADCASTER_PARAMS`` is
    ``0`` / ``false`` / ``no`` / ``off``. The container entrypoint sets
    that: no launcher broadcaster runs in the image, so nothing reads
    the file, and the manager's code dir is not writable there (#296).
    """
    import json
    import tempfile
    from sethlans_manager import runtime_state
    from shared.frozen_paths import get_identity_dir, is_frozen

    flag = os.environ.get(PUBLISH_BROADCASTER_PARAMS_ENV, "")
    if flag.strip().lower() in ("0", "false", "no", "off"):
        logger.info(
            "Not publishing broadcaster params (%s=%s)",
            PUBLISH_BROADCASTER_PARAMS_ENV, flag,
        )
        return

    # Read by the launcher from ``<settings folder>/manager``, the
    # identity folder: it never moves with the data folder.
    if is_frozen():
        data_dir = get_identity_dir("manager")
    else:
        from django.conf import settings
        from sethlans_manager.state_dir import source_data_dir
        data_dir = source_data_dir(settings.BASE_DIR)
    target = data_dir / "broadcaster_params.json"

    payload = {
        "manager_id": runtime_state.manager_id,
        "manager_boot_id": runtime_state.manager_boot_id,
        "name": runtime_state.broadcaster_name or "Sethlans Manager",
        "host": runtime_state.broadcaster_host or "",
        "ip": runtime_state.broadcaster_ip or "0.0.0.0",
        "port": int(runtime_state.broadcaster_port or 8080),
        "version": runtime_state.broadcaster_version or "0.0.0",
    }

    # Atomic write — mkstemp in the same dir, then os.replace.
    parent = str(target.parent)
    fd, tmp_name = tempfile.mkstemp(
        dir=parent, prefix=".broadcaster_", suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        logger.exception(
            "Failed to publish broadcaster params to %s", target,
        )
