// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Types of an Unreal project's measurement summary (`unreal_measurement`
 * of `GET /api/projects/{id}/`), of the machine rows (per-machine memory
 * test FR-MT-U-1) and of the pilot's RAM figures, as built by
 * `manager/workers/serializers/unreal_measurement.py`, `machine_rows.py`
 * and `pilot_ram.py` (Unreal render per card FR-PC-MGR-1, -2, -4).
 */

/** One phase's peak private working set and commit, in MB (1..MAX_PEAK_MB). */
export interface UnrealRamPhase {
  ws_mb: number;
  commit_mb: number;
}

/** The warm-up phase, and whether it ran cold. */
export interface UnrealRamWarmup extends UnrealRamPhase {
  cold: boolean;
}

/** A pilot's RAM figures per phase; `warmup` is null when no warm-up ran. */
export interface UnrealRam {
  prep: UnrealRamPhase;
  warmup: UnrealRamWarmup | null;
  render: UnrealRamPhase;
}

/**
 * A memory test state of one (machine, card kind) pair, in the order of the
 * manager's `PAIR_STATES` (`manager/workers/renderers/unreal_machine.py`).
 */
export type MachineState = 'fits' | 'does_not_fit' | 'testing' | 'failed' | 'retest_due' | 'not_tried';

/**
 * One (machine, card kind) row of a measurement key (per-machine memory test
 * FR-MT-U-1, `MachineRowSerializer` in `manager/workers/serializers/machine_rows.py`).
 */
export interface MachineRow {
  /** Null once the worker was deleted. */
  worker: number | null;
  hostname: string;
  online: boolean;
  /** `vvvv:dddd:rrrr` (PCI vendor, device, revision or `none`). */
  card_kind: string;
  card_name: string | null;
  card_total_mb: number | null;
  state: MachineState;
  /** The result peak; null until measured. */
  peak_mb: number | null;
  /** `does_not_fit`: the need is at least this many MB. */
  lower_bound_mb: number | null;
  driver_version: string | null;
  /** Why the memory test failed (`failed` only). */
  reason: string | null;
  /** Derived from a covering result of the same machine. */
  covered: boolean;
  /** Null until measured and for `not_tried`. */
  ram: UnrealRam | null;
}

/** One `unreal_measurement.keys` entry with its machine list. */
export interface UnrealMeasurementKey {
  asset_id: number;
  target_sha256: string;
  engine_build: string;
  map: string;
  sequence: string;
  preset: string;
  machines: MachineRow[];
}

/** A project's `unreal_measurement`; null for Blender projects. */
export interface UnrealMeasurementSummary {
  held_pending: number;
  held_failed: number;
  held_none: number;
  /** Null in the project list and for worker tokens. */
  keys: UnrealMeasurementKey[] | null;
}
