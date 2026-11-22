// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { UnrealRamComponent, ramPeakText } from './unreal-ram.component';
import { UnrealRam } from '../core/services/unreal-measurement.model';

/** Unreal render per card FR-PC-MGR-4: a machine row's RAM figures. */

const RAM: UnrealRam = {
  prep: { ws_mb: 3000, commit_mb: 4000 },
  warmup: { ws_mb: 9000, commit_mb: 12000, cold: false },
  render: { ws_mb: 8200, commit_mb: 11500 },
};

describe('UnrealRamComponent', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [UnrealRamComponent] }).compileComponents();
  });

  function render(ram: UnrealRam | null): HTMLElement {
    const fixture = TestBed.createComponent(UnrealRamComponent);
    fixture.componentRef.setInput('ram', ram);
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows the render phase RAM peak', () => {
    const el = render(RAM);
    expect(el.querySelector('.ram-peak')!.textContent).toBe('RAM peak 8200 MB physical, 11500 MB commit');
  });

  it('shows nothing when ram is null', () => {
    expect(render(null).querySelector('.measurement-ram')).toBeNull();
  });

  it('gives no text for figures that are not positive integers', () => {
    expect(ramPeakText({ ...RAM, render: { ws_mb: 0, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText({ ...RAM, render: { ws_mb: 1.5, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText({ ...RAM, render: { ws_mb: '5' as unknown as number, commit_mb: 10 } })).toBeNull();
    expect(ramPeakText(undefined)).toBeNull();
  });
});
