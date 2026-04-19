// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { SetupSummary } from '../models/setup.models';

export type RestartOverlayPhase = 'restarting' | 'error';

@Component({
  selector: 'app-setup-restarting-manager',
  standalone: true,
  imports: [
    MatButtonModule,
    MatCardModule,
    MatIconModule,
    MatProgressSpinnerModule,
  ],
  template: `
    @if (phase === 'restarting') {
      <div class="overlay">
        <mat-card>
          <mat-card-content class="overlay-content">
            <mat-spinner mode="indeterminate" diameter="56" />
            <h2>Restarting manager...</h2>
            <p>{{ statusMessage }}</p>
          </mat-card-content>
        </mat-card>
      </div>
    } @else {
      <div class="overlay">
        <mat-card>
          <mat-card-header>
            <mat-icon color="warn">error</mat-icon>
            <mat-card-title>Restart timed out</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <p>The manager did not come back within 2 minutes.</p>
            @if (summary) {
              <div class="summary-row">
                <span class="label">Manager URL</span>
                <span class="value">{{ summary.manager_url }}</span>
              </div>
              <div class="summary-row">
                <span class="label">Certificate Fingerprint</span>
                <span class="value mono fingerprint">{{ summary.cert_fingerprint }}</span>
              </div>
            }
            <p class="hint">
              Check the launcher console for the Setup URL and re-open it.
            </p>
          </mat-card-content>
          <mat-card-actions>
            <button mat-raised-button color="primary" (click)="retryClick.emit()">
              <mat-icon>refresh</mat-icon> Retry
            </button>
          </mat-card-actions>
        </mat-card>
      </div>
    }
  `,
  styles: [`
    .overlay {
      position: fixed;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(255, 255, 255, 0.95);
      z-index: 1000;
      padding: 24px;
    }
    .overlay mat-card { max-width: 560px; width: 100%; }
    .overlay-content {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 16px;
      padding: 32px 16px;
    }
    .summary-row {
      display: flex;
      align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid rgba(0, 0, 0, 0.06);
    }
    .summary-row:last-child { border-bottom: none; }
    .label { font-weight: 500; min-width: 160px; text-align: left; }
    .value { flex: 1; text-align: left; word-break: break-all; }
    .mono { font-family: monospace; font-size: 13px; }
    .fingerprint { font-size: 11px; }
    .hint { color: rgba(0, 0, 0, 0.6); font-size: 13px; }
  `],
})
export class RestartingManagerComponent {
  @Input({ required: true }) phase!: RestartOverlayPhase;
  @Input() statusMessage = 'Waiting for manager to come back online...';
  @Input() summary: SetupSummary | null = null;
  @Output() retryClick = new EventEmitter<void>();
}
