// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input } from '@angular/core';
import { MachineRow } from '../core/services/unreal-measurement.model';
import { machineRowText } from './machine-row-text';
import { UnrealRamComponent } from './unreal-ram.component';

/**
 * The machine list of one Unreal measurement key as plain rows (per-machine
 * memory test FR-MT-U-4): hostname, card name, memory test state, "(off)",
 * "(covered)" and the result's RAM figures. Shows nothing while the list is
 * unknown (null). Farm text is interpolated only. Basic UI only.
 */
@Component({
  selector: 'app-machine-rows',
  standalone: true,
  imports: [UnrealRamComponent],
  template: `
    @if (machines) {
      <div class="machines">
        <div class="machines-title">Machines</div>
        @for (row of machines; track $index) {
          <div class="machine-row">
            <span class="machine-text">{{ text(row) }}</span>
            @if (row.covered) { <small class="machine-covered"> (covered)</small> }
            @if (row.ram) { <span class="machine-ram"> &middot; <app-unreal-ram [ram]="row.ram" /></span> }
          </div>
        } @empty {
          <div class="machines-none">No machines.</div>
        }
      </div>
    }
  `,
  styles: [`.machines { margin: 4px 0; } .machines-title { font-weight: 500; }
    .machine-covered, .machines-none { color: rgba(0,0,0,0.6); }`],
})
export class MachineRowsComponent {
  @Input() machines: MachineRow[] | null | undefined = null;

  readonly text = machineRowText;
}
