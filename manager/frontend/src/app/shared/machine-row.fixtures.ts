// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { MachineRow, UnrealMeasurementKey, UnrealRam } from '../core/services/unreal-measurement.model';

/**
 * Test fixtures for the per-machine memory test rows (FR-MT-U-1), in the
 * shape of `MachineRowSerializer` (`manager/workers/serializers/machine_rows.py`).
 */

export const MACHINE_RAM: UnrealRam = {
  prep: { ws_mb: 3000, commit_mb: 4000 },
  warmup: { ws_mb: 9000, commit_mb: 12000, cold: false },
  render: { ws_mb: 8200, commit_mb: 11500 },
};

export function machineRow(overrides: Partial<MachineRow> = {}): MachineRow {
  return {
    worker: 3, hostname: 'render-01', online: true, card_kind: '1002:744c:c8',
    card_name: 'Radeon RX 7900 XT', card_total_mb: 20464, state: 'fits',
    peak_mb: 13689, lower_bound_mb: null, driver_version: '32.0.21025.10016',
    reason: null, covered: false, ram: null,
    ...overrides,
  };
}

export function measurementKey(overrides: Partial<UnrealMeasurementKey> = {}): UnrealMeasurementKey {
  return {
    asset_id: 5, target_sha256: 'abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789',
    engine_build: '5.8.4-58902052', map: '/Game/Maps/Main', sequence: '/Game/Seq/Ep1',
    preset: '/Game/Presets/Final', machines: [machineRow()],
    ...overrides,
  };
}
