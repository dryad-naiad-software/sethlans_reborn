# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Inputs and outputs of the UE process runner (Unreal foundation FR-UE-11).

``RunSpec`` describes one UE launch; ``RunOutcome`` is what
``process_runner.run`` returns. ``reason`` is one of ``REASONS``:

* ``exited``: the root exited by itself;
* ``exited_after_grace``: the phase's success marker was seen and the
  root had not exited ``quit_grace_s`` later (or a log flood followed the
  marker), so the Job Object was terminated (``quit_forced``);
* ``canceled`` / ``shutdown`` / ``reassigned``: the cancel cause;
* ``timeout``: the wall timeout;
* ``hang``: no new tailed bytes for ``hang_s`` **and** under
  ``HANG_CPU_SECONDS`` of tree CPU over the last ``HANG_CPU_WINDOW_SECONDS``
  (an idle UE uses about 0.9 CPU-s per minute, S3; comparing 5 CPU-s with a
  900 s window could never fire, review D1);
* ``fatal``: a fatal log rule (``fatal_rule``; ``log_flood`` included);
* ``local_conflict``: a per-tick check (``tick_check``, e.g. the farm
  zenserver's loopback rule, FR-UE-26) asked to end the phase;
* ``adapter_mismatch``: the log named another D3D12 adapter than
  ``expected_adapter`` (``adapter_log``, FR-CU-3). ``adapter_chosen`` and
  ``adapter_found`` report the adapter lines of every launch that has one.
"""
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, FrozenSet, Optional, Pattern, Tuple

from sethlans_worker_agent.cards.identity import key_address
from sethlans_worker_agent.unreal_executor.command import UeLaunch
from sethlans_worker_agent.unreal_executor.log_validation import LogRule

REASONS = ('exited', 'exited_after_grace', 'canceled', 'shutdown', 'reassigned', 'timeout', 'hang', 'fatal',
           'local_conflict', 'adapter_mismatch')
CANCEL_CAUSES = ('canceled', 'shutdown', 'reassigned')
HANG_CPU_SECONDS = 5.0           # tree CPU below this over HANG_CPU_WINDOW_SECONDS ... (S1, S3)
HANG_CPU_WINDOW_SECONDS = 60.0   # ... once the log has been silent for hang_s (review D1: 0.9 CPU-s/min idle)
TICK_SECONDS = 2.0               # monitor interval (FR-UE-11)
VRAM_TICK_SECONDS = 1.0          # while sampling GPU memory: at least every 2 s (FR-WD-10)
VRAM_SAMPLE_TIMEOUT_S = 1.5      # each sample's fresh() wait (Conc R3-L6), relative to the sampler's clock
EXIT_WAIT_SECONDS = 30.0         # wait for ActiveProcesses == 0 after termination
FLOOD_WINDOW_SECONDS = 60.0
MB = 1024 * 1024

# Success markers (FR-UE-9, FR-UE-11, S3).
WARMUP_SUCCESS = re.compile(r'\[Warm\] done, quitting')
RENDER_SUCCESS = re.compile(r'RequestExitWithStatus\(0, 0')

UNCONFINED_MESSAGE = "Could not place Unreal in a Windows Job Object; refusing to run unconfined."
# FR-UE-11 survivors: execute() retains the reservation, disables Unreal and fails the job with these.
SURVIVORS_DISABLED_REASON = "orphaned Unreal processes"
SURVIVORS_MESSAGE = "Unreal processes did not exit; Unreal disabled on this worker until restart."


class UnconfinedLaunchError(RuntimeError):
    """FR-UE-11 fail-closed: the process could not be put in a Job Object (it was killed)."""


@dataclass(frozen=True)
class RunSpec:
    launch: UeLaunch
    log_path: Path
    stdout_path: Path
    ledger_path: Path
    timeout_s: float
    hang_s: float
    quit_grace_s: float
    log_max_bytes: int
    log_flood_bytes_per_min: int
    fatal_rules: Tuple[LogRule, ...] = ()
    count_rules: Tuple[LogRule, ...] = ()
    success_marker: Optional[Pattern] = None        # a log line (warm-up, render)
    success_check: Optional[Callable[[], bool]] = None  # e.g. the prep result file reads ok: true
    cancel: Optional[Callable[[], Optional[str]]] = None  # 'canceled' | 'shutdown' | 'reassigned' | None
    shutdown_event: Optional[threading.Event] = None
    excluded_images: frozenset = frozenset()          # normcase-d zenserver image paths (hang CPU)
    vram_sample: Optional[Callable[[float], object]] = None  # timeout_s -> GpuMemoryReading | None
    tick_check: Optional[Callable[[], Optional[str]]] = None  # returns a reason to end the phase
    vram_baseline: Optional[Dict[int, int]] = None    # per-GPU used_mb before the launch
    expected_adapter: Optional[int] = None            # -graphicsadapter=N of a warm-up or render (FR-CU-3)
    expected_ids: Optional[Tuple] = None              # (vendor, device, revision) of the chosen card
    ram_target: Optional[object] = None               # runner_ram.RamTarget: publish to the RAM ledger (FR-PC-RAM-2)
    first_output_dir: Optional[Path] = None           # a render's output folder: the job's card figures from its
    #                                                   first file on (FR-PC-ATT-2)
    on_first_output: Optional[Callable[[], None]] = None  # called once at that first file: the start-up is over
    #                                                   (FR-PC-SU-1)

    @property
    def phase(self) -> str:
        return self.launch.phase


@dataclass
class VramTrace:
    """GPU memory over one phase (FR-UE-11 VRAM peak; FR-UE-25 inputs)."""
    samples: int = 0
    max_used: Dict[int, int] = field(default_factory=dict)
    min_used: Dict[int, int] = field(default_factory=dict)
    total: Dict[int, int] = field(default_factory=dict)
    max_sethlans: Dict[int, int] = field(default_factory=dict)  # largest attributed sethlans_used_mb per GPU
    max_gap_s: float = 0.0
    last_sample_at: Optional[float] = None
    # The launch's own Job Object members (FR-PC-ATT-2), from the reading's per-pid map:
    max_job: Dict[int, int] = field(default_factory=dict)  # their largest MiB per GPU of the reading
    job_peak_mb: Optional[int] = None         # on the launch's card, from the first output file on
    job_other_cards_mb: Optional[int] = None  # the largest on any other card, from the first output file on
    job_unknown: bool = False                 # a sample in that window could not be attributed

    def add(self, reading, own_pids: Optional[FrozenSet[int]] = None, window: bool = False) -> None:
        """Add one reading, timed by its own ``taken_at``; a reading already added is skipped.

        ``own_pids``: the launch's members at this tick (``None``: no per-job attribution); ``window``: the
        render has written its first output file.
        """
        if reading is None or reading.taken_at == self.last_sample_at:
            return
        if self.last_sample_at is not None:
            self.max_gap_s = max(self.max_gap_s, reading.taken_at - self.last_sample_at)
        self.last_sample_at = reading.taken_at
        self.samples += 1
        for gpu in reading.gpus:
            self.max_used[gpu.index] = max(self.max_used.get(gpu.index, gpu.used_mb), gpu.used_mb)
            self.min_used[gpu.index] = min(self.min_used.get(gpu.index, gpu.used_mb), gpu.used_mb)
            self.total[gpu.index] = gpu.total_mb
            ours = getattr(gpu, 'sethlans_used_mb', None)
            if ours is not None:
                self.max_sethlans[gpu.index] = max(self.max_sethlans.get(gpu.index, 0), ours)
        if own_pids is not None:
            self._attribute(reading, own_pids, window)

    def _attribute(self, reading, own_pids: FrozenSet[int], window: bool) -> None:
        """The members' MiB per card; ``per_pid`` holds every card of the machine, ``gpus`` the launch's own."""
        per_pid = reading.per_pid
        mine = {key_address(gpu.card_key): gpu.index for gpu in reading.gpus}
        if per_pid is None or not set(mine) <= {key_address(key) for key in per_pid}:
            self.job_unknown = self.job_unknown or window
            return
        for key, by_pid in per_pid.items():
            used = sum(mib for pid, mib in by_pid.items() if pid in own_pids)
            index = mine.get(key_address(key))
            if index is not None:
                self.max_job[index] = max(self.max_job.get(index, 0), used)
                if window:
                    self.job_peak_mb = max(self.job_peak_mb or 0, used)
            elif window:
                self.job_other_cards_mb = max(self.job_other_cards_mb or 0, used)
        if window and self.job_other_cards_mb is None:
            self.job_other_cards_mb = 0  # no other card on the machine

    def peak_over(self, baseline: Dict[int, int]) -> Optional[int]:
        """Largest ``max_used - baseline`` over GPUs (at least 0), or None without samples."""
        if not self.max_used:
            return None
        return max(max(0, used - baseline.get(index, 0)) for index, used in self.max_used.items())


@dataclass
class RunOutcome:
    exit_code: Optional[int]
    reason: str
    rule_counts: Dict[str, int]
    wall_s: float
    survivors: Tuple[Tuple[int, str], ...]
    vram_peak_mb: Optional[int] = None
    phase: str = ''
    success_marker_seen: bool = False
    quit_forced: bool = False
    fatal_rule: str = ''
    detail: str = ''
    job_object: str = 'ok'
    vram: VramTrace = field(default_factory=VramTrace)
    adapter_chosen: Optional[int] = None   # the "Chosen D3D12 Adapter Id" of the log (expected launches only)
    adapter_found: bool = False            # the log has "Found D3D12 adapter" lines
    adapter_problem: str = ''              # what the log chose instead of the expected card ('' when it matched)
    ram: Optional[object] = None           # runner_ram.PhaseRam, set after the exit is confirmed (FR-PC-RAM-2)
    shader_workers: bool = False           # a ShaderCompileWorker was a member: the launch compiled shaders (D8)
