# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The pure parts of the E2E GPU memory holder (``tests/e2e/gpu_memory_holder.py``):
argument limits, chunking, matching the card by PCI address only (never a
fallback), the size cap, the stop rule, the status file and the hold loop
with a stand-in for the Direct3D 12 memory.
"""

import json
import os
import subprocess
import sys

import psutil
import pytest

from sethlans_worker_agent.cards.os_probe import OsCard
from tests.e2e import gpu_memory_holder as holder

CARD = OsCard(key="pci:0000:01:00.0", name="Card A", vendor_id=0x1234, device_id=0x5678, dxgi_index=0,
              dedicated_mib=32000)
OTHER = OsCard(key="pci:0000:00:02.0", name="Card B", vendor_id=0x4321, device_id=0x0001, dxgi_index=1,
               dedicated_mib=512)
PAIRS = ((CARD, (0, 100)), (OTHER, (0, 200)))


def _args(status, *extra):
    return holder.parse_args(["--card", CARD.key, "--mb", "1536", "--seconds", "30", "--status", str(status),
                              *extra])


def test_arguments_keep_their_hard_limits(tmp_path):
    args = _args(tmp_path / "s.json", "--watch-pid=7", "--watch-pid=9")
    assert (args.mb, args.seconds, args.chunk_mb, args.watch_pid) == (1536, 30.0, holder.DEFAULT_CHUNK_MB, [7, 9])
    for bad in (["--mb", "0"], ["--mb", str(holder.MAX_MB + 1)], ["--seconds", "0"],
                ["--seconds", str(holder.MAX_SECONDS + 1)], ["--chunk-mb", "2048"]):
        with pytest.raises(SystemExit):
            _args(tmp_path / "s.json", *bad)


def test_chunks_add_up_to_the_request():
    assert holder.chunk_sizes(1536, 256) == [256] * 6
    assert holder.chunk_sizes(1000, 256) == [256, 256, 256, 232]
    assert holder.chunk_sizes(100, 256) == [100]


def test_the_card_is_matched_by_pci_address_only():
    assert holder.match_card("pci:0000:01:00.0", PAIRS) == (CARD, (0, 100))
    assert holder.match_card("pci:0000:00:02.0", PAIRS) == (OTHER, (0, 200))


@pytest.mark.parametrize("key, pairs", [
    ("pci:0000:02:00.0", PAIRS),            # no card there: never another adapter
    ("Card A", PAIRS),                       # a name is no identity
    (CARD.key, None),                        # no card list
    (CARD.key, PAIRS + ((CARD, (0, 300)),)),  # two adapters with the address
])
def test_no_single_match_is_refused(key, pairs):
    with pytest.raises(holder.HolderError):
        holder.match_card(key, pairs)


def test_size_is_capped_by_the_card():
    holder.check_size(16000, CARD)
    with pytest.raises(holder.HolderError):
        holder.check_size(16001, CARD)


def test_stop_rule():
    alive = {7: True, 9: False}.get
    assert holder.stop_reason(5.0, 10.0, [7], alive) is None
    assert holder.stop_reason(10.0, 10.0, [7], alive) == "seconds"
    assert holder.stop_reason(5.0, 10.0, [7, 9], alive) == "pid 9 ended"


class _Memory:
    def __init__(self, fail_at=None):
        self.fail_at, self.sizes, self.freed, self.opened = fail_at, [], 0, False

    def open(self):
        self.opened = True

    def allocate(self, nbytes):
        if len(self.sizes) == self.fail_at:
            raise RuntimeError("out of memory")
        self.sizes.append(nbytes)

    def free(self):
        self.freed += 1


def test_hold_allocates_holds_and_frees(tmp_path, monkeypatch):
    monkeypatch.setattr(holder, "_alive", lambda pid: False)  # the watched launch ended: stop at once
    path = tmp_path / "s.json"
    status, memory = holder.Status(path, card=CARD.key), _Memory()
    assert holder._hold(_args(path, "--watch-pid=7"), status, memory) == 0
    assert memory.sizes == [256 * holder.MIB] * 6 and memory.freed == 1
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["state"], data["allocated_mb"], data["stop_reason"]) == ("freed", 1536, "pid 7 ended")
    assert data["pid"] > 0 and "held_at" in data and "freed_at" in data


def test_a_failed_allocation_frees_and_fails_loudly(tmp_path):
    path = tmp_path / "s.json"
    status, memory = holder.Status(path), _Memory(fail_at=2)
    assert holder._hold(_args(path), status, memory) == holder.EXIT_FAILED
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data["state"], data["allocated_mb"], memory.freed) == ("failed", 512, 1)
    assert "out of memory" in data["error"]


def test_main_refuses_an_unknown_card(tmp_path, monkeypatch):
    from sethlans_worker_agent.cards import os_probe_windows
    monkeypatch.setattr(holder.sys, "platform", "win32")
    monkeypatch.setattr(os_probe_windows, "cards_with_luids", lambda: PAIRS)
    path = tmp_path / "s.json"
    code = holder.main(["--card", "pci:0000:03:00.0", "--mb", "1024", "--seconds", "5", "--status", str(path)])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert code == holder.EXIT_REFUSED and data["state"] == "refused" and "0000:03:00.0" in data["error"]


def test_alive_sees_a_running_process_and_an_ended_one():
    holder._watched.clear()
    assert holder._alive(os.getpid()) is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=30)
    assert holder._alive(child.pid) is False  # our Popen handle is still open: the pid may still be listed


class _Proc:
    def __init__(self, running=True, status="running", wait=None):
        self.running, self._status, self._wait = running, status, wait

    def is_running(self):
        return self.running

    def status(self):
        return self._status

    def wait(self, timeout):
        if self._wait is not None:
            raise self._wait
        return 0


def test_alive_handles_psutil_outcomes(monkeypatch):
    cases = [(_Proc(wait=psutil.TimeoutExpired(0)), True), (_Proc(), False), (_Proc(running=False), False),
             (_Proc(status=psutil.STATUS_ZOMBIE), False), (_Proc(wait=psutil.AccessDenied(1)), True)]
    for proc, expected in cases:
        holder._watched.clear()
        holder._watched[1] = proc
        assert holder._alive(1) is expected, (vars(proc), expected)
    holder._watched.clear()

    def gone(pid):
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", gone)
    assert holder._alive(2) is False


def test_status_replace_retries_a_busy_file(tmp_path, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(holder.os, "replace", flaky)
    monkeypatch.setattr(holder, "REPLACE_RETRY_S", 0)
    holder.Status(tmp_path / "s.json", card=CARD.key)
    assert len(calls) == 3 and json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))["card"] == CARD.key
