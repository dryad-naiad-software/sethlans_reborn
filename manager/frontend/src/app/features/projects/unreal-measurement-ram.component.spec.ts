// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { UnrealMeasurementRamComponent, ramPeakText } from './unreal-measurement-ram.component';
import { UnrealLatestMeasurement, UnrealRam } from '../../core/services/unreal-measurement.model';

/** Unreal render per card FR-PC-MGR-4: the project page's RAM line. */

const RAM: UnrealRam = {
  prep: { ws_mb: 3000, commit_mb: 4000 },
  warmup: { ws_mb: 9000, commit_mb: 12000, cold: false },
  render: { ws_mb: 8200, commit_mb: 11500 },
};

function makeLatest(overrides: Partial<UnrealLatestMeasurement> = {}): UnrealLatestMeasurement {
  return {
    measurement_id: 7, status: 'measured', reason: null, remeasure: false, asset_id: 5,
    engine_build: '5.8.4-58902052', map: '/Game/Maps/Main', sequence: '/Game/Seq/Shot', preset: '/Game/Preset',
    peak_mb: 14000, peak_source: 'card', game_overrides: 'off', texture_streaming: 'on',
    gate_need_mb: 15400, measured_at: '2026-11-21T10:00:00Z', worker_hostname: 'render-01',
    gpu_name: 'RX 7900 XTX', gpu_total_mb: 24560, prep_s: 30, warmup_s: 12, seconds_per_frame: 4,
    pilot_job_id: 41, ram: RAM, ram_process_commit_peak_mb: 11000, shared_machine: false,
    ...overrides,
  };
}

describe('UnrealMeasurementRamComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [UnrealMeasurementRamComponent] }).compileComponents();
  });

  function render(latest: UnrealLatestMeasurement | null): HTMLElement {
    const fixture = TestBed.createComponent(UnrealMeasurementRamComponent);
    fixture.componentRef.setInput('latest', latest);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the render phase RAM peak', () => {
    const el = render(makeLatest());
    expect(el.querySelector('.ram-peak')!.textContent).toBe('RAM peak 8200 MB physical, 11500 MB commit');
    expect(el.querySelector('.ram-shared')).toBeNull();
  });

  it('says when the machine was shared', () => {
    const el = render(makeLatest({ shared_machine: true }));
    expect(el.querySelector('.ram-peak')!.textContent).toContain('RAM peak 8200 MB physical');
    expect(el.querySelector('.ram-shared')!.textContent).toContain('measured while the machine was shared');
  });

  it('shows nothing when ram is null, even when shared', () => {
    expect(render(makeLatest({ ram: null, shared_machine: true })).querySelector('.measurement-ram')).toBeNull();
  });

  it('shows nothing when there is no latest measurement', () => {
    expect(render(null).querySelector('.measurement-ram')).toBeNull();
  });

  it('gives no text for figures that are not positive integers', () => {
    expect(ramPeakText({ ...RAM, render: { ws_mb: 0, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText({ ...RAM, render: { ws_mb: 1.5, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText({ ...RAM, render: { ws_mb: '5' as unknown as number, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText(undefined)).toBeNull();
  });
});
