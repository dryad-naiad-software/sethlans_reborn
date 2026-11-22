// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { UnrealMeasurementKey } from '../../core/services/unreal-measurement.model';
import { MachineRowsComponent } from '../../shared/machine-rows.component';

/**
 * The project page's memory tests (per-machine memory test FR-MT-U-4): each
 * measurement key under the current CVars with its machine rows. Shows
 * nothing when there are no keys. Text is interpolated only. Basic UI only.
 */
@Component({
  selector: 'app-unreal-measurement-keys',
  standalone: true,
  imports: [MachineRowsComponent],
  template: `
    @if (keys && keys.length > 0) {
      <div class="measurement-keys">
        <h3>Memory tests</h3>
        @for (key of keys; track key.asset_id + ':' + key.target_sha256) {
          <div class="measurement-key">
            <div class="key-name">{{ key.sequence }} &middot; {{ key.map }} &middot; {{ key.preset }}
              &middot; {{ key.engine_build }} &middot; target {{ key.target_sha256.slice(0, 12) }}</div>
            <app-machine-rows [machines]="key.machines" />
          </div>
        }
      </div>
    }
  `,
  styles: [`.measurement-keys h3 { margin: 8px 0 4px; } .measurement-key { margin-bottom: 8px; }
    .key-name { color: rgba(0,0,0,0.6); }`],
})
export class UnrealMeasurementKeysComponent {
  @Input() keys: UnrealMeasurementKey[] | null | undefined = null;
}
