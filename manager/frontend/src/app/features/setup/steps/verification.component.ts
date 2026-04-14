// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output, OnInit } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { VerifyCheck } from '../models/setup.models';

@Component({
  selector: 'app-setup-verification',
  standalone: true,
  imports: [
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>Verification</h2>
    <p class="description">Verifying your setup configuration...</p>

    @if (loading) {
      <div class="loading"><mat-spinner diameter="40" /></div>
    } @else {
      <div class="check-list">
        @for (check of checks; track check.name) {
          <div class="check-item">
            @if (check.passed) {
              <mat-icon class="check-icon passed">check_circle</mat-icon>
            } @else {
              <mat-icon class="check-icon failed">error</mat-icon>
            }
            <div class="check-info">
              <span class="check-name">{{ check.name }}</span>
              @if (check.error) {
                <span class="check-error">{{ check.error }}</span>
              }
            </div>
          </div>
        }
      </div>

      @if (allPassed) {
        <div class="banner passed-banner">
          <mat-icon>verified</mat-icon>
          <span>All checks passed</span>
        </div>
      } @else {
        <div class="banner failed-banner">
          <mat-icon>warning</mat-icon>
          <span>Some checks failed. Review the errors above.</span>
        </div>
        <button mat-raised-button color="primary" (click)="runVerification()">
          <mat-icon>refresh</mat-icon> Retry
        </button>
      }
    }
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .loading { display: flex; justify-content: center; padding: 48px; }
    .check-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 24px;
    }
    .check-item { display: flex; align-items: flex-start; gap: 12px; }
    .check-icon.passed { color: #4caf50; }
    .check-icon.failed { color: #f44336; }
    .check-info { display: flex; flex-direction: column; }
    .check-name { font-weight: 500; }
    .check-error {
      font-size: 13px;
      color: #f44336;
      margin-top: 2px;
    }
    .banner {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 16px;
      border-radius: 4px;
      margin-bottom: 16px;
      font-weight: 500;
    }
    .passed-banner {
      background: #e8f5e9;
      color: #2e7d32;
    }
    .failed-banner {
      background: #fff3e0;
      color: #e65100;
    }
  `],
})
export class VerificationComponent implements OnInit {
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();

  loading = true;
  checks: VerifyCheck[] = [];
  allPassed = false;

  ngOnInit(): void {
    this.runVerification();
  }

  runVerification(): void {
    this.loading = true;
    this.api.verify().subscribe({
      next: (res) => {
        this.loading = false;
        this.checks = res.checks;
        this.allPassed = res.all_passed;
        if (res.all_passed) {
          this.state.markCheckpoint('verified');
          this.stepComplete.emit();
        }
      },
      error: (err) => {
        this.loading = false;
        const msg = err.error?.detail || 'Verification failed';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }
}
