// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { UnrealMeasurementKey } from '../../core/services/unreal-measurement.model';
import { MachineRowsComponent } from '../../shared/machine-rows.component';
import { machineRow, measurementKey } from '../../shared/machine-row.fixtures';
import { UnrealMeasurementKeysComponent } from './unreal-measurement-keys.component';
import { HOSTILE, text } from './sequence.fixtures';

/** Per-machine memory test FR-MT-U-4: the project page's machine rows per measurement key. */

describe('UnrealMeasurementKeysComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [UnrealMeasurementKeysComponent] }).compileComponents();
  });

  function render(keys: UnrealMeasurementKey[] | null | undefined) {
    const fixture = TestBed.createComponent(UnrealMeasurementKeysComponent);
    fixture.componentRef.setInput('keys', keys);
    fixture.detectChanges();
    return fixture;
  }

  function el(keys: UnrealMeasurementKey[] | null | undefined): HTMLElement {
    return render(keys).nativeElement as HTMLElement;
  }

  it('shows nothing when keys are null', () => {
    const page = el(null);
    expect(page.querySelector('.measurement-keys')).toBeNull();
    expect(text(page)).toBe('');
  });

  it('shows nothing when keys are undefined', () => {
    expect(el(undefined).querySelector('.measurement-keys')).toBeNull();
  });

  it('shows nothing when there are no keys', () => {
    const page = el([]);
    expect(page.querySelector('.measurement-keys')).toBeNull();
    expect(page.querySelector('h3')).toBeNull();
  });

  it('shows the "Memory tests" heading', () => {
    expect(text(el([measurementKey()]).querySelector('h3'))).toBe('Memory tests');
  });

  it('names the key by sequence, map, preset, engine build and the first 12 target characters', () => {
    expect(text(el([measurementKey()]).querySelector('.key-name')))
      .toBe('/Game/Seq/Ep1 · /Game/Maps/Main · /Game/Presets/Final · 5.8.4-58902052 · target abcdef012345');
  });

  it('shows each key with its own machine rows, in order', () => {
    const first = measurementKey({ sequence: '/Game/Seq/Ep1', machines: [machineRow({ hostname: 'render-01' })] });
    const second = measurementKey({
      sequence: '/Game/Seq/Ep2', target_sha256: '1234567890abcdef'.repeat(4),
      machines: [machineRow({ hostname: 'render-02', state: 'not_tried', online: false })],
    });
    const page = el([first, second]);
    const keys = Array.from(page.querySelectorAll('.measurement-key'));
    expect(keys.length).toBe(2);
    expect(text(keys[0].querySelector('.key-name'))).toContain('/Game/Seq/Ep1');
    expect(text(keys[1].querySelector('.key-name'))).toContain('target 1234567890ab');
    expect(text(keys[0].querySelector('.machine-text'))).toBe('render-01, Radeon RX 7900 XT: fits: 13,689 of 20,464 MB');
    expect(text(keys[1].querySelector('.machine-text'))).toBe('render-02, Radeon RX 7900 XT: not tried yet (off)');
  });

  it('passes each key\'s machine list to the machine rows', () => {
    const machines = [machineRow(), machineRow({ hostname: 'render-02' })];
    const rows = render([measurementKey({ machines })]).debugElement.queryAll(By.directive(MachineRowsComponent));
    expect(rows.length).toBe(1);
    expect((rows[0].componentInstance as MachineRowsComponent).machines).toBe(machines);
  });

  it('says "No machines." for a key without machines', () => {
    const page = el([measurementKey({ machines: [] })]);
    expect(text(page.querySelector('.machines-none'))).toBe('No machines.');
  });

  it('shows key names as text only', () => {
    const page = el([measurementKey({ sequence: HOSTILE, map: HOSTILE, preset: HOSTILE, engine_build: HOSTILE })]);
    expect(text(page.querySelector('.key-name'))).toContain(HOSTILE);
    expect(page.querySelector('img, b')).toBeNull();
  });
});
