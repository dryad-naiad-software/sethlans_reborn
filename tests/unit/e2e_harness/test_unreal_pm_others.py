# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Part B's opt-in other program (``tests/e2e/unreal_pm_others.py``,
``unreal_pm_config.others_mb``): the opt-in value, finding this machine's
pilot render launch from a command line, its card by DXGI index among the
ticked cards only, starting once, and the band 2 checks on the pilot.
"""

import subprocess

import psutil
import pytest

from sethlans_worker_agent.cards.os_probe import OsCard
from tests.e2e import unreal_pm_config as pm
from tests.e2e import unreal_pm_others as others

CARD = OsCard(key="pci:0000:01:00.0", name="Card A", dxgi_index=0, dedicated_mib=32000)
RENDER = ["D:\\UE\\UnrealEditor-Cmd.exe", "D:\\c\\work\\P.uproject", "-dx12", "-graphicsadapter=0",
          "-abslog=D:\\c\\j\\42-1a2b3c4d\\logs\\render.1.log"]


def test_others_mb_is_opt_in_and_bounded():
    assert pm.others_mb({}) is None and pm.others_mb({pm.OTHERS_MB_ENV: " "}) is None
    assert pm.others_mb({pm.OTHERS_MB_ENV: "1536"}) == 1536
    for bad in ("512", "abc", str(pm.OTHERS_MAX_MB + 1)):
        with pytest.raises(AssertionError):
            pm.others_mb({pm.OTHERS_MB_ENV: bad})


def test_render_launch_from_the_command_line():
    assert others.render_launch(RENDER) == (42, "1a2b3c4d", 0)
    assert others.render_launch(['"-abslog=D:/c/j/7-00ff/logs/render.2.log"']) == (7, "00ff", None)


@pytest.mark.parametrize("cmdline", [
    RENDER[:-1] + ["-abslog=D:\\c\\j\\42-1a2b3c4d\\logs\\warmup.1.log"],
    RENDER[:-1] + ["-abslog=D:\\c\\j\\42-1a2b3c4d\\logs\\prep.1.log"],
    RENDER[:-1],
    None,
])
def test_other_launches_are_not_render_launches(cmdline):
    assert others.render_launch(cmdline) is None


def test_card_by_dxgi_index_among_the_ticked_cards_only():
    assert others.card_for_index([CARD], 0) is CARD
    for index in (1, None):
        with pytest.raises(AssertionError):
            others.card_for_index([CARD], index)


def test_poll_starts_once_inside_this_machines_pilot_render(tmp_path, monkeypatch):
    launches = [(11, 41, "aa", 0), (12, 42, "1a2b3c4d", 0)]
    started = []
    monkeypatch.setattr(others, "render_launches", lambda: launches)
    monkeypatch.setattr(others.rec, "pilot_target", lambda db, job: {41: 2, 42: 5}[job])
    holder = others.OthersHolder(1536, [CARD], tmp_path / "db", 5, tmp_path, 512)
    monkeypatch.setattr(holder, "_start", lambda *a: (started.append(a), setattr(holder, "proc", object())))
    holder.poll()
    holder.poll()
    assert started == [(12, 42, "1a2b3c4d", CARD)]


def test_stop_without_a_start_is_empty(tmp_path):
    assert others.OthersHolder(1536, [CARD], tmp_path / "db", 5, tmp_path, 512).stop() == {}


def _summary(**values):
    base = {"attempt": "1a2b3c4d", "pilot_others_rise_mb": "1550", "pilot_others_added_mb": "0",
            "pilot_peak_mb": "4100", "pilot_card_delta_peak_mb": "4100", "pilot_csv_peak_mb": "3900",
            "pilot_peak_source": "card", "pilot_verdict": "fits", "pilot_lower_bound_mb": ""}
    base.update(values)
    return "\n".join(f"[sethlans-unreal] {k}={v}" for k, v in base.items())


@pytest.fixture
def band2(tmp_path, monkeypatch):
    monkeypatch.setattr(others.rec, "record", lambda *a, **k: None)
    monkeypatch.setattr(others.rec, "pilot_record",
                        lambda db, job: {"verdict": "fits", "peak_mb": 4100, "lower_bound_mb": None})
    holder = others.OthersHolder(1536, [CARD], tmp_path / "db", 5, tmp_path, 512)
    holder.trigger = {"job": 42, "attempt": "1a2b3c4d"}
    status = {"state": "freed", "allocated_mb": 1536}
    return holder, status, {"peak_mb": 4100, "lower_bound_mb": None}


BAND2_LOG = ("[x] [INFO] [pilot_flow] Pilot job 42: other programs' use of the pilot's graphics card rose by 1550 MB "
             "during the render (more than the 512 MB margin) while the card stayed clear of full; the part ...")


def test_the_band2_line_matches_the_workers_message():
    from sethlans_worker_agent.unreal_executor import pilot_flow
    logged = "Pilot job %s: %s" % (42, pilot_flow.OTHERS_ADDED_MESSAGE.format(
        rise=1550, phase="render", margin=512, top=2400))
    assert others.BAND2_LINE.format(job=42, rise=1550) in logged and others.BAND2_TAIL in logged


def test_band2_pilot_passes(band2):
    holder, status, row = band2
    pilot = {"id": 42, "last_output": _summary()}
    assert others.check_pilot(holder, status, pilot, BAND2_LOG, row) == {"rise_mb": 1550, "added_mb": 0}


@pytest.mark.parametrize("summary, log", [
    (_summary(attempt="99999999"), BAND2_LOG),                            # released and run again
    (_summary(), BAND2_LOG + "\nWARNING Pilot job 42 records no verdict: x"),  # band 3
    (_summary(pilot_others_rise_mb="300"), BAND2_LOG),                    # band 1: no rise seen
    (_summary(pilot_others_rise_mb=""), BAND2_LOG),                       # not checked
    (_summary(), ""),                                                     # the added key alone: no band 2 line
    (_summary(), BAND2_LOG.replace("job 42:", "job 43:")),                # another pilot's line
    (_summary(pilot_peak_mb="4200"), BAND2_LOG),                          # peak is not max(card, csv)
    (_summary(pilot_peak_source="csv"), BAND2_LOG),
])
def test_band2_failures_are_loud(band2, summary, log):
    holder, status, row = band2
    with pytest.raises(AssertionError):
        others.check_pilot(holder, status, {"id": 42, "last_output": summary}, log, row)


def test_a_holder_that_never_ran_in_the_pilot_fails(band2):
    holder, status, row = band2
    holder.trigger = None
    with pytest.raises(AssertionError):
        others.check_pilot(holder, status, {"id": 42, "last_output": _summary()}, BAND2_LOG, row)
    holder.trigger = {"job": 42, "attempt": "1a2b3c4d"}
    with pytest.raises(AssertionError):
        others.check_pilot(holder, {"state": "failed", "allocated_mb": 512}, {"id": 42, "last_output": _summary()},
                           BAND2_LOG, row)


def test_margin_is_the_worker_default_and_never_overridden():
    assert others.pilot_margin_mb({}, {}) == 512
    for environ, worker_env in (({others.MARGIN_ENV: "256"}, {}), ({}, {others.MARGIN_ENV: "1024"})):
        with pytest.raises(AssertionError):
            others.pilot_margin_mb(environ, worker_env)


class _Popen:
    pid = 77

    def __init__(self):
        self.killed = False

    def poll(self):
        return None

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        raise subprocess.TimeoutExpired("holder", timeout)


class _StuckHolder:
    def cmdline(self):
        return ["python", str(others.HOLDER)]

    def kill(self):
        pass

    def wait(self, timeout):
        raise psutil.TimeoutExpired(timeout, 88)


def test_stop_timeouts_are_logged_not_raised(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(psutil, "Process", lambda pid: _StuckHolder())
    holder = others.OthersHolder(1536, [CARD], tmp_path / "db", 5, tmp_path, 512)
    holder.status_path.write_text('{"state": "freed", "pid": 88}', encoding="utf-8")
    holder.proc = _Popen()
    assert holder.stop() == {"state": "freed", "pid": 88}
    assert holder.proc.killed and "pid 88" in caplog.text and "pid 77" in caplog.text
