// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Types of an Unreal project's measurement summary (`unreal_measurement`
 * of `GET /api/projects/{id}/`) and of the pilot's RAM figures, as built by
 * `manager/workers/serializers/unreal_measurement.py` and `pilot_ram.py`
 * (Unreal render per card FR-PC-MGR-1, -2, -4).
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

/** `unreal_measurement.latest`: the latest measurement under the current CVars. */
export interface UnrealLatestMeasurement {
  measurement_id: number;
  status: 'measured' | 'pending' | 'failed';
  reason: string | null;
  remeasure: boolean;
  asset_id: number;
  engine_build: string;
  map: string;
  sequence: string;
  preset: string;
  peak_mb: number | null;
  peak_source: string | null;
  game_overrides: string | null;
  texture_streaming: string | null;
  gate_need_mb: number | null;
  measured_at: string | null;
  worker_hostname: string | null;
  gpu_name: string | null;
  gpu_total_mb: number | null;
  prep_s: number | null;
  warmup_s: number | null;
  seconds_per_frame: number | null;
  pilot_job_id: number | null;
  /** Null until measured and on records without RAM figures. */
  ram: UnrealRam | null;
  ram_process_commit_peak_mb: number | null;
  /** The pilot ran while another job held the worker; null until measured. */
  shared_machine: boolean | null;
}

/** A project's `unreal_measurement`; null for Blender projects. */
export interface UnrealMeasurementSummary {
  held_pending: number;
  held_failed: number;
  held_none: number;
  latest: UnrealLatestMeasurement | null;
}
