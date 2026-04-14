// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit } from '@angular/core';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { SetupSummary } from '../models/setup.models';

@Component({
  selector: 'app-setup-done',
  standalone: true,
  imports: [
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <div class="done-container">
      <mat-icon class="done-icon">check_circle</mat-icon>
      <h1>Setup Complete</h1>

      @if (loading) {
        <mat-spinner diameter="40" />
      } @else if (summary) {
        <div class="summary-card">
          <div class="summary-row">
            <span class="label">Manager URL</span>
            <span class="value">{{ summary.manager_url }}</span>
            <button mat-icon-button (click)="copyToClipboard(summary.manager_url)"
                    aria-label="Copy URL">
              <mat-icon>content_copy</mat-icon>
            </button>
          </div>
          <div class="summary-row">
            <span class="label">Admin Username</span>
            <span class="value">{{ summary.admin_username }}</span>
            <button mat-icon-button (click)="copyToClipboard(summary.admin_username)"
                    aria-label="Copy username">
              <mat-icon>content_copy</mat-icon>
            </button>
          </div>
          <div class="summary-row">
            <span class="label">Enrollment Key</span>
            <span class="value mono">{{ summary.enrollment_key }}</span>
            <button mat-icon-button (click)="copyToClipboard(summary.enrollment_key)"
                    aria-label="Copy enrollment key">
              <mat-icon>content_copy</mat-icon>
            </button>
          </div>
          <div class="summary-row">
            <span class="label">Certificate Fingerprint</span>
            <span class="value mono fingerprint">{{ summary.cert_fingerprint }}</span>
            <button mat-icon-button (click)="copyToClipboard(summary.cert_fingerprint)"
                    aria-label="Copy fingerprint">
              <mat-icon>content_copy</mat-icon>
            </button>
          </div>
        </div>

        <button mat-raised-button color="primary" (click)="openSethlans()">
          <mat-icon>launch</mat-icon> Open Sethlans
        </button>
      }
    </div>
  `,
  styles: [`
    .done-container {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      padding: 32px 24px;
    }
    .done-icon {
      font-size: 72px;
      width: 72px;
      height: 72px;
      color: #4caf50;
      margin-bottom: 16px;
    }
    .summary-card {
      width: 100%;
      max-width: 560px;
      margin: 24px 0;
      border: 1px solid rgba(0, 0, 0, 0.12);
      border-radius: 8px;
      overflow: hidden;
    }
    .summary-row {
      display: flex;
      align-items: center;
      padding: 12px 16px;
      border-bottom: 1px solid rgba(0, 0, 0, 0.06);
    }
    .summary-row:last-child { border-bottom: none; }
    .label {
      font-weight: 500;
      min-width: 160px;
      text-align: left;
    }
    .value {
      flex: 1;
      text-align: left;
      word-break: break-all;
    }
    .mono { font-family: monospace; font-size: 13px; }
    .fingerprint { font-size: 11px; }
  `],
})
export class DoneComponent implements OnInit {
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);

  loading = true;
  summary: SetupSummary | null = null;

  ngOnInit(): void {
    this.state.clearSensitiveData();
    this.api.getSummary().subscribe({
      next: (s) => {
        this.summary = s;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.snackBar.open('Failed to load summary', 'Dismiss', { duration: 5000 });
      },
    });
  }

  copyToClipboard(text: string): void {
    navigator.clipboard.writeText(text).then(
      () => this.snackBar.open('Copied to clipboard', 'Dismiss', { duration: 2000 }),
      () => this.snackBar.open('Failed to copy', 'Dismiss', { duration: 3000 }),
    );
  }

  openSethlans(): void {
    this.router.navigate(['/']);
  }
}
