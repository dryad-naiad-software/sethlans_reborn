# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The two-machine harness of the per-machine memory test E2E, Part B
(Unreal per-machine memory test chunk 9, AC-E7, AC-E8).

The least that makes a real second card kind: this machine runs the test,
the manager and one local worker as every real Unreal E2E does; a second
machine runs one more worker against this manager through the normal
enrollment flow.

- The manager's TLS front (Caddy) already listens on every interface; here
  it uses the fixed port ``SETHLANS_UNREAL_E2E_PM_PORT`` instead of a free
  one. The operator allows that port inbound, TCP, from the second
  machine's address only, and removes the rule after the run (the exact
  ``New-NetFirewallRule`` / ``Remove-NetFirewallRule`` lines are in
  ``unreal_pm_config``). Run it on a trusted LAN only: the second worker
  enrolls with the run's key over that network. The test itself still
  talks to ``127.0.0.1``.
- Because the manager is reachable from the LAN, the admin account gets a
  password made for this run (``secrets.token_urlsafe``, never logged)
  instead of the harness's fixed test password, and the manager runs with
  ``SETHLANS_SECURITY_DEBUG=false``, the production default. It sets
  Django's ``DEBUG``, so error pages with debug detail are not shown to the
  LAN (WhiteNoise's autorefresh also follows it). Nothing in the harness
  needs ``DEBUG`` on.
- After the local worker is ready, ``_write_handoff`` writes what the second
  worker needs to a local JSON file (``SETHLANS_UNREAL_E2E_PM_HANDOFF``,
  default in the temp dir) and logs it: the manager address
  (``SETHLANS_UNREAL_E2E_PM_MANAGER_HOST``), the port, this run's
  enrollment key and the share the manager uses. The worker enrolls with
  the key and pins the manager's certificate from the signed enrollment
  answer, as any new worker does. The file is deleted at teardown (it holds
  the run's enrollment key); it is never written to the share.
- On the second machine the operator starts ``tests/e2e/unreal_pm_remote_worker.py``
  with those values (see its docstring).
- ``_remote`` waits, bounded by ``SETHLANS_UNREAL_E2E_PM_REMOTE_WAIT_S``
  (default 45 min), for a second active worker that reports Unreal ready on
  the same share and whose ticked cards are of exactly one card kind, other
  than this machine's (kinds from the heartbeat ``card_status``, D1; never
  from names). The test assumes one pilot per machine and card kind.
"""

import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from tests.e2e import unreal_pm_config as pm
from tests.e2e.helpers import admin_login
from tests.e2e.process_manager import (
    ADMIN_USERNAME, build_manager_env, generate_secrets, manager_session, setup_database,
    start_manager, wait_for_manager,
)
from tests.e2e.unreal_e2e_config import ENGINE_BUILD
from tests.e2e.unreal_pm_flow import MachineTestFlow
from tests.e2e.unreal_workflow_harness import _unreal_ready

logger = logging.getLogger(__name__)

_REMOTE_POLL_S = 10


def handoff_path():
    return Path(os.environ.get(pm.HANDOFF_ENV, "").strip()
                or Path(tempfile.gettempdir(), "sethlans-unreal-pm-handoff.json"))


class TwoMachineFlow(MachineTestFlow):
    """``MachineTestFlow`` with a LAN-reachable manager on a fixed port and a second, remote worker."""

    piece_seconds = pm.PIECE_SECONDS_B
    _remote_worker = None

    @classmethod
    def _card_env(cls):
        cards, reason = pm.choose_one_kind(1)
        return (None, reason) if reason else (pm.worker_env(cards), None)

    @classmethod
    def _cache_dir(cls):
        return pm.cache_dir()

    @classmethod
    def _start_manager(cls):
        """As the harness, on the fixed port the second machine connects to, with a per-run admin password.

        A copy rather than a hook in ``UnrealWorkflowHarness``: that file is at the 300-line limit, and a
        hook would need three overrides (port, admin password, DEBUG) to replace this method.
        """
        cls.port = int(os.environ[pm.PORT_ENV])
        cls.base_url = f"https://127.0.0.1:{cls.port}"
        cls.test_secrets = generate_secrets()
        media_root = cls.temp_dir / "media"
        media_root.mkdir(parents=True, exist_ok=True)
        cls.manager_env = build_manager_env(cls.temp_dir / "test_e2e_db.sqlite3", media_root,
                                            cls.test_secrets["enrollment_key"], cls.test_secrets["secret_key"],
                                            cls.port)
        password = secrets.token_urlsafe(24)  # the manager is on the LAN: no fixed test password (never logged)
        cls.manager_env.update({"DJANGO_SUPERUSER_PASSWORD": password, "SETHLANS_SECURITY_DEBUG": "false"})
        setup_database(cls.manager_env)
        cls.manager_proc = start_manager(cls.manager_env, cls.port)
        wait_for_manager(cls.base_url, proc=cls.manager_proc)
        cls.session = manager_session(cls.manager_env)
        admin_login(cls.session, cls.base_url, ADMIN_USERNAME, password)

    @classmethod
    def _start_unreal_worker(cls):
        super()._start_unreal_worker()
        cls._write_handoff()

    @classmethod
    def _write_handoff(cls):
        handoff = {"manager_host": os.environ[pm.MANAGER_HOST_ENV].strip(), "port": cls.port,
                   "enrollment_key": cls.test_secrets["enrollment_key"], "share": cls.share,
                   "engine_build": ENGINE_BUILD, "written_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        path = handoff_path()
        path.write_text(json.dumps(handoff, indent=2), encoding="utf-8")
        logger.warning(
            "SECOND MACHINE: start its worker now (within %s s). Handoff file: %s\n"
            "  manager host %s, port %s, enrollment key %s\n"
            "  on the second machine: python tests/e2e/unreal_pm_remote_worker.py --handoff <copy of the file>\n"
            "  or set %s, %s and %s there and run it without --handoff.",
            cls._remote_wait_s(), path, handoff["manager_host"], handoff["port"], handoff["enrollment_key"],
            pm.MANAGER_HOST_ENV, pm.PORT_ENV, pm.ENROLLMENT_KEY_ENV)

    @classmethod
    def teardown_class(cls):
        try:
            handoff_path().unlink(missing_ok=True)
        finally:
            super().teardown_class()

    @staticmethod
    def _remote_wait_s():
        text = os.environ.get(pm.REMOTE_WAIT_ENV, "").strip()
        return int(text) if text.isdigit() else pm.DEFAULT_REMOTE_WAIT_S

    def _local_kind(self):
        kinds = self.ticked_kinds(self._worker(self.worker_data["hostname"]))
        assert len(kinds) == 1, f"This machine's ticked cards are not of one kind: {kinds}"
        return next(iter(kinds))

    def _remote(self):
        """``(remote worker, its card kind)``: the second machine's worker, Unreal ready, of another kind."""
        cls = type(self)
        if cls._remote_worker is not None:
            return cls._remote_worker
        local, local_kind = self.worker_data["hostname"], self._local_kind()
        deadline, seen = time.monotonic() + self._remote_wait_s(), []
        while time.monotonic() < deadline:
            seen = [w for w in self._workers() if w.get("hostname") != local]
            for worker in seen:
                kinds = self.ticked_kinds(worker)
                if _unreal_ready(worker, self.share) and len(kinds) == 1 and local_kind not in kinds:
                    logger.info("Second machine %s ready, card kind %s", worker["hostname"], kinds)
                    cls._remote_worker = (worker, next(iter(kinds)))
                    return cls._remote_worker
            time.sleep(_REMOTE_POLL_S)
        reported = [(w.get("hostname"), w.get("is_active"), w.get("unreal_capabilities"),
                     sorted(self.ticked_kinds(w))) for w in seen]
        raise AssertionError(f"No second machine with Unreal ready on the share and one other card kind within "
                             f"{self._remote_wait_s()} s (this machine's kind {local_kind}): {reported}")

    def _cards_total(self, hostname):
        """The largest total memory of a worker's cards (its heartbeat GPU reading), for the log."""
        gpus = (self._worker(hostname).get("gpu_memory") or {}).get("gpus") or []
        return max((gpu.get("total_mb") or 0 for gpu in gpus), default=0)
