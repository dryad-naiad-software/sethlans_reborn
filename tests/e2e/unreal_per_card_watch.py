# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Samples taken on every poll of the render-per-card E2E (Unreal render per
card AC-E1, AC-E3, AC-E4, AC-E6).

``PerCardWatch.sample()``:

- **Card holds:** the worker's heartbeat card report (``card_status.cards``,
  FR-PC-MGR-3) as ``{card key: job id}`` for every card an Unreal job holds.
  Two different jobs on two cards in one sample is the worker's own proof
  that two Unreal renders ran at once, one per card.
- **Per-slot TEMP:** for every farm editor (``UnrealEditor-Cmd``) and
  ShaderCompileWorker under the worker, the slot is read from the editor's
  ``-ZenDataPath=`` (the slot's Zen data folder, D5; a worker's parent
  editor for a ShaderCompileWorker) and its ``TEMP`` and ``TMP`` must be
  that slot's ``<profile>\\Temp``: UE puts its shader working folder under
  ``GetTempPath`` (``TMP``, then ``TEMP``).
- **RAM of the farm tree:** the summed ``PrivateWorkingSetSize`` and
  ``PrivateUsage`` of the farm processes (the worker's own reader,
  ``process_memory.private_ws_and_commit``), kept with the jobs held at
  that moment, so a pilot's figures can be compared while it ran alone.

Everything here only reads.
"""

import logging
import ntpath
import time
from collections import Counter

import psutil

logger = logging.getLogger(__name__)

EDITOR = "unrealeditor-cmd.exe"
SCW = "shadercompileworker.exe"
FARM = frozenset((EDITOR, SCW, "zenserver.exe"))
_ZEN_FLAG = "-zendatapath="


def _norm(path):
    return ntpath.normcase(ntpath.normpath(path.strip().strip('"'))) if path else ""


def _zen_path(cmdline):
    for token in cmdline or ():
        if token.lower().startswith(_ZEN_FLAG):
            return _norm(token.split("=", 1)[1])
    return ""


class PerCardWatch:
    """Card holds, per-slot TEMP and the farm tree's RAM, one record per poll."""

    def __init__(self, session, base_url, hostname, worker_pid, cache_dir, slots=2):
        from sethlans_worker_agent.unreal_executor import user_state, zen_private
        self.session, self.base_url, self.hostname = session, base_url, hostname
        self.worker_pid = worker_pid
        self.slot_of_zen = {_norm(zen_private.zen_data_dir(cache_dir, n)): n for n in range(slots)}
        self.temp_of_slot = {n: _norm(ntpath.join(user_state.profile_dir(cache_dir, n), "Temp"))
                             for n in range(slots)}
        self.holds = []          # (monotonic time, {card key: job id})
        self.tree = []           # (frozenset of held job ids, ws bytes, commit bytes)
        self.temp_ok = Counter()  # (slot, image) -> processes seen with their own slot's TEMP
        self.temp_problems = []
        self.unreadable = Counter()
        self._seen = set()

    # -- card holds --------------------------------------------------------
    def _card_holds(self):
        resp = self.session.get(f"{self.base_url}/api/heartbeat/", timeout=30)
        resp.raise_for_status()
        for worker in resp.json():
            if worker.get("hostname") == self.hostname:
                cards = (worker.get("card_status") or {}).get("cards") or []
                return {c["key"]: c["job_id"] for c in cards if c.get("renderer") == "UNREAL" and c.get("job_id")}
        return {}

    # -- processes ---------------------------------------------------------
    def _farm_processes(self):
        try:
            children = psutil.Process(self.worker_pid).children(recursive=True)
        except psutil.Error:
            return []
        return [proc for proc in children if self._name(proc) in FARM]

    @staticmethod
    def _name(proc):
        try:
            return proc.name().lower()
        except psutil.Error:
            return ""

    def _slot(self, proc):
        """The slot of a farm editor, or of a ShaderCompileWorker's parent editor; ``None`` when unknown."""
        try:
            editor = proc if self._name(proc) == EDITOR else proc.parent()
            return self.slot_of_zen.get(_zen_path(editor.cmdline())) if editor else None
        except psutil.Error:
            return None

    def _check_temp(self, proc, name):
        if proc.pid in self._seen:
            return
        slot = self._slot(proc)
        try:
            env = proc.environ()
        except psutil.Error:
            self.unreadable[name] += 1
            return
        if slot is None:
            self.unreadable[name] += 1
            return
        self._seen.add(proc.pid)
        temps = {_norm(env.get("TEMP", "")), _norm(env.get("TMP", ""))}
        if temps == {self.temp_of_slot[slot]}:
            self.temp_ok[(slot, name)] += 1
        else:
            self.temp_problems.append(f"{name} {proc.pid} of slot {slot}: TEMP/TMP {sorted(temps)}")

    @staticmethod
    def _memory(proc):
        from sethlans_worker_agent.unreal_executor.process_memory import private_ws_and_commit
        try:
            return private_ws_and_commit(proc.pid)
        except Exception:  # noqa: BLE001 - a process that exited between the walk and the read
            return 0, 0

    def sample(self):
        holds = self._card_holds()
        self.holds.append((time.monotonic(), holds))
        ws = commit = 0
        for proc in self._farm_processes():
            name = self._name(proc)
            if name in (EDITOR, SCW):
                self._check_temp(proc, name)
            proc_ws, proc_commit = self._memory(proc)
            ws, commit = ws + proc_ws, commit + proc_commit
        if commit:
            self.tree.append((frozenset(holds.values()), ws, commit))

    # -- reading -----------------------------------------------------------
    def held(self, job_id, key):
        """True when a sample showed ``job_id`` holding card ``key`` as an Unreal job."""
        return any(holds.get(key) == job_id for _t, holds in self.holds)

    def together(self):
        """``{(job, job)}``: pairs of Unreal jobs seen holding two cards in the same sample."""
        pairs = set()
        for _t, holds in self.holds:
            jobs = sorted(set(holds.values()))
            pairs.update((a, b) for i, a in enumerate(jobs) for b in jobs[i + 1:])
        return pairs

    def shared_while(self, job_id):
        """True when another Unreal job held a card in a sample in which ``job_id`` held one."""
        return any(job_id in holds.values() and len(set(holds.values())) > 1 for _t, holds in self.holds)

    def alone_peaks_mb(self, job_id):
        """``(ws, commit)`` MB: the largest farm-tree sums sampled while ``job_id`` was the only job; ``None`` without."""
        rows = [(ws, commit) for jobs, ws, commit in self.tree if jobs == {job_id}]
        if not rows:
            return None
        return max(ws for ws, _c in rows) // 2 ** 20, max(commit for _w, commit in rows) // 2 ** 20
