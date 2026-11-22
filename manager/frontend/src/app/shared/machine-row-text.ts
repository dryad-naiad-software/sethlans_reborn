// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { MachineRow } from '../core/services/unreal-measurement.model';

const MEGABYTES = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });

/** A size in MB with thousands separators ("20,464"), or "unknown" when missing (NF-2). */
export function megabytesText(value: number | null | undefined): string {
  return typeof value === 'number' && Number.isFinite(value) ? MEGABYTES.format(value) : 'unknown';
}

/**
 * The memory test state of one machine row (per-machine memory test
 * FR-MT-U-4), e.g. "fits: 13,689 of 20,464 MB". An unknown state is shown
 * as such, never as another state.
 */
export function machineStateText(row: MachineRow): string {
  switch (row.state) {
    case 'fits':
      return `fits: ${megabytesText(row.peak_mb)} of ${megabytesText(row.card_total_mb)} MB`;
    case 'does_not_fit':
      return `does not fit: at least ${megabytesText(row.lower_bound_mb)} MB on a `
        + `${megabytesText(row.card_total_mb)} MB card`;
    case 'testing':
      return 'testing';
    case 'failed':
      return `test failed: ${row.reason || 'no reason given'}`;
    case 'retest_due':
      return 're-test due (driver changed)';
    case 'not_tried':
      return 'not tried yet';
    default:
      return `unknown state: ${String(row.state)}`;
  }
}

/**
 * The whole row text: "<hostname>, <card name>: <state>", with " (off)"
 * when the machine is offline. The card kind stands in for a missing card name.
 */
export function machineRowText(row: MachineRow): string {
  const card = row.card_name || row.card_kind;
  return `${row.hostname}, ${card}: ${machineStateText(row)}${row.online ? '' : ' (off)'}`;
}
