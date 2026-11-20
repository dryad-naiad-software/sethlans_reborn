# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Fakes for the Windows ``pdh`` backend tests: a scripted ``PdhQuery``, a card list and counter items.

Every name, LUID and card key is made up; no vendor is assumed. Runs on every OS.
"""
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.gpu_memory import win_backend
from sethlans_worker_agent.gpu_memory.win_pdh import PDH_CSTATUS_VALID_DATA, PDH_FMT_LARGE

MIB = 1024 * 1024
ADAPTER = win_backend.ADAPTER_PATH
PROCESS = win_backend.PROCESS_PATH
OURS = frozenset({4242, 4343})
OTHER_PID = 777

KEY_A = 'pci:0000:0a:00.0'
KEY_B = 'pci:0000:0b:00.0'
LUID_A = (0, 0x0001A2B3)
LUID_B = (0, 0x0001C4D5)


def os_card(index, key, name='Test Card A', mib=8192):
    return OsCard(key=key, name=name, vendor_id=0x1234, device_id=0x5678, dxgi_index=index, dedicated_mib=mib)


def two_cards():
    return ((os_card(0, KEY_A, 'Test Card A', 8192), LUID_A), (os_card(2, KEY_B, 'Test Card B', 4096), LUID_B))


def hex_luid(luid, upper=True):
    high, low = luid
    text = f"0x{high & 0xFFFFFFFF:08x}_0x{low & 0xFFFFFFFF:08x}"
    return text.upper().replace('0X', '0x') if upper else text


def adapter_item(luid, mib, phys=0, status=PDH_CSTATUS_VALID_DATA, upper=True):
    return (f"luid_{hex_luid(luid, upper)}_phys_{phys}", mib * MIB, status)


def process_item(pid, luid, mib, phys=0, status=PDH_CSTATUS_VALID_DATA):
    return (f"pid_{pid}_luid_{hex_luid(luid)}_phys_{phys}", mib * MIB, status)


def collect(adapters, processes=()):
    """A collect result; ``processes=None`` means the process array failed."""
    return {ADAPTER: list(adapters), PROCESS: None if processes is None else list(processes)}


class FakeQuery:
    """A scripted ``PdhQuery``: ``collects`` entries are dicts or None; the last one repeats."""

    def __init__(self, collects):
        self.collects = list(collects)
        self.calls = 0
        self.closed = 0
        self.paths = None

    def __call__(self, paths):
        self.paths = tuple(paths)
        return self

    def collect(self):
        self.calls += 1
        return self.collects.pop(0) if len(self.collects) > 1 else self.collects[0]

    def close(self):
        self.closed += 1


class FakeCards:
    """``cards_with_luids`` returning ``results`` in turn (the last repeats); a result may be an exception."""

    def __init__(self, *results):
        self.results = list(results) or [two_cards()]
        self.calls = 0

    def __call__(self):
        self.calls += 1
        value = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(value, Exception):
            raise value
        return value


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeApi:
    """The five PDH calls for a real ``win_pdh.PdhQuery``; ``fail`` names the call that returns an error status.

    ``arrays`` maps a counter handle (1, 2, ... in the order added) to a ``PdhGetFormattedCounterArrayW`` fake.
    """

    def __init__(self, fail=None, arrays=None):
        self.fail, self.arrays, self.log = fail, arrays or {}, []

    def _status(self, name):
        self.log.append(name)
        return 0xC0000BB8 if self.fail == name else 0

    def open_query(self, source, user, handle):
        handle._obj.value = 1234
        return self._status('open')

    def add_counter(self, query, path, user, counter):
        counter._obj.value = 1 + len([entry for entry in self.log if entry.startswith('add')])
        return self._status('add ' + path)

    def collect(self, query):
        return self._status('collect')

    def get_array(self, counter, fmt, size, count, buffer):
        assert fmt == PDH_FMT_LARGE
        return self.arrays[counter.value](counter, size._obj, count._obj, buffer)

    def close_query(self, query):
        return self._status('close')


def make_backend(query, cards=None, clock=None, is_64bit=True, checks=None):
    """A ``PdhBackend`` over fakes; ``checks`` (a list) records the cross-check calls."""
    checks = [] if checks is None else checks
    return win_backend.PdhBackend(cards=cards or FakeCards(), query_factory=query, clock=clock or Clock(),
                                  is_64bit=is_64bit, cross_check=checks.append)
