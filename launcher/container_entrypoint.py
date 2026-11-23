# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Entrypoint for the manager Docker image (issue #296).

On a native install the launcher starts Caddy (TLS on the public port)
and then runs the manager as a child process. The manager image has no
launcher main loop, so this module does the same job in the container:

1. Resolve the TLS cert and key exactly as the manager does (BYO via
   ``SETHLANS_TLS_CERT_FILE`` / ``SETHLANS_TLS_KEY_FILE``, else a
   self-signed pair in ``SETHLANS_TLS_DATA_DIR``), so Caddy has them
   before it starts.
2. Start Caddy through the shared :class:`CaddySupervisor`, built by
   :func:`launcher.caddy_launcher.build_manager_caddy_supervisor`,
   with the static Caddyfile named by ``SETHLANS_MANAGER_CADDYFILE_PATH``.
3. Run ``manager/run_manager.py`` (Waitress on loopback) as a child.
   Any command-line arguments (for example ``--dev``) are passed on.

Then it waits:

* SIGTERM / SIGINT (``docker stop``): stop the manager and Caddy, exit 0.
* The manager exits on its own: stop Caddy, exit with the manager's code.
* Caddy gives up after its restart budget: stop the manager, exit 1.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANAGER_DIR = PROJECT_ROOT / "manager"


class StopFlag:
    """Set by the signal handler, read by the main loop.

    A plain attribute, not a ``threading.Event``: ``Event.set()`` takes
    a lock, and a signal landing while the main thread holds that lock
    inside ``Event.wait()`` would deadlock.
    """

    def __init__(self) -> None:
        self.requested = False


_STOP = StopFlag()


def install_signal_handlers(stop: StopFlag) -> None:
    """Set ``stop.requested`` on SIGTERM and SIGINT.

    PID 1 in a container gets no default signal action, so without a
    handler ``docker stop`` would wait out its timeout and SIGKILL.
    The handler only sets the flag; :func:`supervise` does the rest.
    """
    def _handler(_signum, _frame):
        stop.requested = True

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


if __name__ == "__main__":
    # Before the heavier imports below, so an early docker stop is seen.
    install_signal_handlers(_STOP)

# Run as a script (``python /app/launcher/container_entrypoint.py``):
# put the project root (for ``launcher`` / ``shared``) and ``manager/``
# (for ``sethlans_manager``) on sys.path, as run_manager.py does.
for _path in (str(MANAGER_DIR), str(PROJECT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from launcher.caddy_launcher import (  # noqa: E402
    CADDYFILE_PATH_ENV,
    build_manager_caddy_supervisor,
)
from launcher.setup_helpers import (  # noqa: E402
    CADDY_LOOPBACK_PORT,
    CADDY_PUBLIC_TLS_PORT,
)
from sethlans_manager.runtime_init import (  # noqa: E402
    PUBLISH_BROADCASTER_PARAMS_ENV,
)
from sethlans_manager.tls_setup import (  # noqa: E402
    get_config_file_path,
    setup_certificates,
)
from sethlans_manager.waitress_config import (  # noqa: E402
    get_waitress_internal_port,
    get_waitress_public_port,
)
from shared.cert_utils import CertificateError  # noqa: E402

logger = logging.getLogger("sethlans.container")

RUN_MANAGER_PY = MANAGER_DIR / "run_manager.py"
POLL_SECONDS = 0.5
# ``docker stop`` waits 10 s before SIGKILL; stay inside that.
CADDY_STOP_TIMEOUT = 3.0
MANAGER_STOP_TIMEOUT = 5.0
EXIT_CONFIG_ERROR = 2


def resolve_public_port(ini_path: Path) -> int:
    """Return Caddy's public TLS port.

    Same order the manager uses for its advertised port:
    ``SETHLANS_MANAGER_PORT`` > ``manager.ini [server] port`` > 8080.
    """
    raw = os.environ.get("SETHLANS_MANAGER_PORT")
    if not raw and ini_path.is_file():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(ini_path)
        raw = cfg.get("server", "port", fallback=None)
    if not raw:
        return CADDY_PUBLIC_TLS_PORT
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"manager port is not a number: {raw!r}") from None


def build_supervisor():
    """Resolve cert, key and ports; return a ready-to-start supervisor."""
    caddyfile = os.environ.get(CADDYFILE_PATH_ENV)
    if not caddyfile:
        raise ValueError(
            f"{CADDYFILE_PATH_ENV} is not set; this entrypoint is for "
            "the manager container image, which ships a static Caddyfile"
        )
    cert_path, key_path, _cert = setup_certificates(
        dev_mode=False, manager_dir=MANAGER_DIR, project_root=PROJECT_ROOT,
    )
    ini_path = get_config_file_path(MANAGER_DIR)
    return build_manager_caddy_supervisor(
        caddyfile_path=Path(caddyfile),
        public_tls_port=resolve_public_port(ini_path),
        loopback_plaintext_port=CADDY_LOOPBACK_PORT,
        cert_path=Path(cert_path),
        key_path=Path(key_path),
        manager_data_dir=MANAGER_DIR,
        waitress_public_port=get_waitress_public_port(ini_path),
        waitress_internal_port=get_waitress_internal_port(ini_path),
    )


def spawn_manager(args: List[str]) -> subprocess.Popen:
    """Start ``run_manager.py`` with the container's stdio and env.

    No launcher broadcaster runs in the container, so the manager is
    told not to publish ``broadcaster_params.json`` into its (read-only)
    code dir.
    """
    cmd = [sys.executable, str(RUN_MANAGER_PY), *args]
    env = dict(os.environ)
    env[PUBLISH_BROADCASTER_PARAMS_ENV] = "0"
    logger.info("Starting manager: %s", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=str(MANAGER_DIR), env=env)


def stop_all(manager: Optional[subprocess.Popen], caddy) -> None:
    """Stop the manager (SIGTERM, then SIGKILL) and Caddy."""
    if manager is not None and manager.poll() is None:
        manager.terminate()
    caddy.stop(timeout=CADDY_STOP_TIMEOUT)
    if manager is None:
        return
    try:
        manager.wait(timeout=MANAGER_STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        logger.warning(
            "Manager did not exit within %.0fs; killing it",
            MANAGER_STOP_TIMEOUT,
        )
        manager.kill()
        manager.wait()


def exit_code_for(returncode: int) -> int:
    """Map a child's return code to a shell-style exit code."""
    if returncode < 0:
        return 128 - returncode
    return returncode


def supervise(manager: subprocess.Popen, caddy, stop: StopFlag) -> int:
    """Wait for a stop signal, a manager exit or a Caddy failure."""
    while True:
        if stop.requested:
            logger.info("Stop signal received; stopping manager and Caddy")
            stop_all(manager, caddy)
            return 0
        returncode = manager.poll()
        if returncode is not None:
            logger.error("Manager exited (code %s); stopping Caddy", returncode)
            stop_all(None, caddy)
            return exit_code_for(returncode)
        if caddy.error_event.is_set():
            logger.error("Caddy failed and will not restart; stopping manager")
            stop_all(manager, caddy)
            return 1
        time.sleep(POLL_SECONDS)


def main(
    argv: Optional[List[str]] = None, stop: Optional[StopFlag] = None,
) -> int:
    """Start Caddy and the manager, then supervise both."""
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    args = list(sys.argv[1:] if argv is None else argv)
    stop = _STOP if stop is None else stop
    install_signal_handlers(stop)
    try:
        caddy = build_supervisor()
    except (CertificateError, ValueError) as exc:
        logger.error("Cannot start: %s", exc)
        return EXIT_CONFIG_ERROR
    try:
        caddy.start()
    except Exception:
        logger.exception("Could not start Caddy; not starting the manager")
        return 1
    if stop.requested:
        stop_all(None, caddy)
        return 0
    try:
        manager = spawn_manager(args)
    except OSError:
        logger.exception("Could not start the manager")
        stop_all(None, caddy)
        return 1
    return supervise(manager, caddy, stop)


if __name__ == "__main__":
    sys.exit(main())
