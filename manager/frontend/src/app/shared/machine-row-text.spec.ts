// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { MachineRow, MachineState } from '../core/services/unreal-measurement.model';
import { machineRowText, machineStateText, megabytesText } from './machine-row-text';
import { machineRow } from './machine-row.fixtures';

/** Per-machine memory test FR-MT-U-4 (and NF-2): the texts of one machine row. */

describe('megabytesText', () => {
  it('adds thousands separators', () => {
    expect(megabytesText(20464)).toBe('20,464');
    expect(megabytesText(1234567)).toBe('1,234,567');
  });

  it('leaves small values without a separator', () => {
    expect(megabytesText(999)).toBe('999');
    expect(megabytesText(0)).toBe('0');
  });

  it('shows no fraction digits', () => {
    expect(megabytesText(13689.4)).toBe('13,689');
  });

  it('says "unknown" for a missing or non-finite size', () => {
    expect(megabytesText(null)).toBe('unknown');
    expect(megabytesText(undefined)).toBe('unknown');
    expect(megabytesText(Number.NaN)).toBe('unknown');
    expect(megabytesText(Number.POSITIVE_INFINITY)).toBe('unknown');
    expect(megabytesText('20464' as unknown as number)).toBe('unknown');
  });
});

describe('machineStateText', () => {
  it('shows a fit with the peak and the card size', () => {
    expect(machineStateText(machineRow())).toBe('fits: 13,689 of 20,464 MB');
  });

  it('shows a misfit with the lower bound and the card size', () => {
    const row = machineRow({ state: 'does_not_fit', peak_mb: null, lower_bound_mb: 19012 });
    expect(machineStateText(row)).toBe('does not fit: at least 19,012 MB on a 20,464 MB card');
  });

  it('shows "testing"', () => {
    expect(machineStateText(machineRow({ state: 'testing', peak_mb: null }))).toBe('testing');
  });

  it('shows a failed test with its reason', () => {
    const row = machineRow({ state: 'failed', peak_mb: null, reason: 'Unreal exited with code 3' });
    expect(machineStateText(row)).toBe('test failed: Unreal exited with code 3');
  });

  it('says "no reason given" for a failed test without a reason', () => {
    expect(machineStateText(machineRow({ state: 'failed', reason: null }))).toBe('test failed: no reason given');
    expect(machineStateText(machineRow({ state: 'failed', reason: '' }))).toBe('test failed: no reason given');
  });

  it('shows a re-test due after a driver change', () => {
    expect(machineStateText(machineRow({ state: 'retest_due' }))).toBe('re-test due (driver changed)');
  });

  it('shows "not tried yet"', () => {
    expect(machineStateText(machineRow({ state: 'not_tried', peak_mb: null }))).toBe('not tried yet');
  });

  it('says "unknown" for missing figures instead of hiding them (NF-2)', () => {
    expect(machineStateText(machineRow({ peak_mb: null, card_total_mb: null }))).toBe('fits: unknown of unknown MB');
    const misfit = machineRow({ state: 'does_not_fit', lower_bound_mb: null, card_total_mb: null });
    expect(machineStateText(misfit)).toBe('does not fit: at least unknown MB on a unknown MB card');
  });

  it('shows an unknown state as such, never as another state', () => {
    const row = machineRow({ state: 'measuring' as unknown as MachineState });
    expect(machineStateText(row)).toBe('unknown state: measuring');
    expect(machineStateText(machineRow({ state: null as unknown as MachineState }))).toBe('unknown state: null');
  });
});

describe('machineRowText', () => {
  it('joins hostname, card name and state', () => {
    expect(machineRowText(machineRow())).toBe('render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB');
  });

  it('adds "(off)" when the machine is offline', () => {
    expect(machineRowText(machineRow({ online: false })))
      .toBe('render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB (off)');
  });

  it('adds no "(off)" while the machine is online', () => {
    expect(machineRowText(machineRow({ online: true }))).not.toContain('(off)');
  });

  it('uses the card kind when the card name is missing', () => {
    expect(machineRowText(machineRow({ card_name: null, state: 'not_tried' })))
      .toBe('render-01, 1002:744c:c8: not tried yet');
    expect(machineRowText(machineRow({ card_name: '', state: 'testing' })))
      .toBe('render-01, 1002:744c:c8: testing');
  });

  it('keeps every state text in the row', () => {
    const cases: [Partial<MachineRow>, string][] = [
      [{ state: 'does_not_fit', lower_bound_mb: 19012 }, 'does not fit: at least 19,012 MB on a 20,464 MB card'],
      [{ state: 'testing' }, 'testing'],
      [{ state: 'failed', reason: 'crash' }, 'test failed: crash'],
      [{ state: 'retest_due' }, 're-test due (driver changed)'],
      [{ state: 'not_tried' }, 'not tried yet'],
    ];
    for (const [overrides, state] of cases) {
      expect(machineRowText(machineRow({ ...overrides, online: false })))
        .toBe(`render-01, Radeon RX 7900 XT: ${state} (off)`);
    }
  });
});
