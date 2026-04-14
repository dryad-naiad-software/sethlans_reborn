// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Topology } from '../models/setup.models';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';

@Component({
  selector: 'app-setup-topology',
  standalone: true,
  imports: [
    MatCardModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Select Topology</h2>
    <p class="description">Choose how this machine will operate in your render farm.</p>

    @if (submitting) {
      <div class="loading"><mat-spinner diameter="40" /></div>
    } @else {
      <div class="topology-grid">
        <mat-card class="topology-card" (click)="onSelect('manager')"
                  [class.selected]="selected === 'manager'">
          <mat-card-header>
            <mat-icon mat-card-avatar>dns</mat-icon>
            <mat-card-title>Manager Only</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <p>Manages projects and distributes jobs to remote workers.
               Does not render locally.</p>
          </mat-card-content>
        </mat-card>

        <mat-card class="topology-card" (click)="onSelect('manager_worker')"
                  [class.selected]="selected === 'manager_worker'">
          <mat-card-header>
            <mat-icon mat-card-avatar>hub</mat-icon>
            <mat-card-title>Manager + Worker</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <p>Manages projects and also renders jobs locally.
               Best for single-machine setups or small farms.</p>
          </mat-card-content>
        </mat-card>

        <mat-card class="topology-card disabled">
          <mat-card-header>
            <mat-icon mat-card-avatar>memory</mat-icon>
            <mat-card-title>Worker Only</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <p>To set up a worker-only node, install Sethlans on the worker
               machine and run the setup wizard there.</p>
          </mat-card-content>
        </mat-card>
      </div>
    }
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .loading { display: flex; justify-content: center; padding: 48px; }
    .topology-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }
    .topology-card {
      cursor: pointer;
      transition: border-color 0.2s, box-shadow 0.2s;
      border: 2px solid transparent;
    }
    .topology-card:hover {
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
    }
    .topology-card.selected {
      border-color: #1976d2;
    }
    .topology-card.disabled {
      opacity: 0.6;
      cursor: default;
    }
    .topology-card.disabled:hover {
      box-shadow: none;
    }
    mat-card-content p { color: rgba(0, 0, 0, 0.7); }
  `],
})
export class TopologyComponent {
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();
  selected: Topology | null = null;
  submitting = false;

  onSelect(topology: Topology): void {
    this.selected = topology;
    this.submitting = true;
    this.api.setTopology({ topology }).subscribe({
      next: () => {
        this.state.setTopology(topology);
        this.state.markCheckpoint('topology_chosen');
        this.submitting = false;
        this.stepComplete.emit();
      },
      error: (err) => {
        this.submitting = false;
        const msg = err.error?.detail || 'Failed to set topology';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }
}
