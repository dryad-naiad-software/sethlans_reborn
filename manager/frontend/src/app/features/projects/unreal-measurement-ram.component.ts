// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { UnrealLatestMeasurement, UnrealRam } from '../../core/services/unreal-measurement.model';

/** A RAM figure the manager accepts: an integer of at least 1 MB. */
function isMegabytes(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1;
}

/**
 * FR-PC-MGR-4: "RAM peak <ws> MB physical, <commit> MB commit" of the
 * render phase, or `null` when there are no usable RAM figures.
 */
export function ramPeakText(ram: UnrealRam | null | undefined): string | null {
  const render = ram?.render;
  if (!render || !isMegabytes(render.ws_mb) || !isMegabytes(render.commit_mb)) return null;
  return `RAM peak ${render.ws_mb} MB physical, ${render.commit_mb} MB commit`;
}

/**
 * The RAM line of a project's latest Unreal measurement (FR-PC-MGR-4).
 * Shows nothing when the measurement has no RAM figures. Basic UI only.
 */
@Component({
  selector: 'app-unreal-measurement-ram',
  standalone: true,
  template: `
    @if (text) {
      <p class="measurement-ram">
        <span class="ram-peak">{{ text }}</span>
        @if (shared) {
          <span class="ram-shared">, measured while the machine was shared</span>
        }
      </p>
    }
  `,
  styles: [`
    .measurement-ram { color: rgba(0,0,0,0.6); margin: 0 0 4px; }
  `],
})
export class UnrealMeasurementRamComponent {
  @Input() latest: UnrealLatestMeasurement | null = null;

  get text(): string | null { return ramPeakText(this.latest?.ram); }

  get shared(): boolean { return this.latest?.shared_machine === true; }
}
