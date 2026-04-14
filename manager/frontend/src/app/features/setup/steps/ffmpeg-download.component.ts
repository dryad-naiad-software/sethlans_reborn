// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, output, OnInit, OnDestroy } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subscription, interval, switchMap, takeWhile } from 'rxjs';
import { SetupApiService } from '../services/setup-api.service';
import { SetupStateService } from '../services/setup-state.service';
import { DownloadProgress } from '../models/setup.models';

@Component({
  selector: 'app-setup-ffmpeg-download',
  standalone: true,
  imports: [
    MatButtonModule,
    MatIconModule,
    MatProgressBarModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  template: `
    <h2>FFmpeg Installation</h2>
    <p class="description">FFmpeg is required for video encoding support.</p>

    @if (starting) {
      <div class="loading"><mat-spinner diameter="40" /></div>
    } @else if (failed) {
      <div class="status-section">
        <mat-icon class="status-icon error">error</mat-icon>
        <p class="error-text">{{ errorText }}</p>
        <button mat-raised-button color="primary" (click)="retry()">
          <mat-icon>refresh</mat-icon> Retry
        </button>
      </div>
    } @else if (progress) {
      <div class="progress-section">
        <p class="status-text">{{ statusLabel }}</p>
        @if (progress.status === 'downloading') {
          <mat-progress-bar mode="determinate" [value]="progress.percent" />
          <p class="percent-text">{{ progress.percent }}%</p>
        } @else {
          <mat-progress-bar mode="indeterminate" />
        }
        <button mat-button color="warn" (click)="cancel()">Cancel</button>
      </div>
    }
  `,
  styles: [`
    .description { color: rgba(0, 0, 0, 0.6); margin-bottom: 24px; }
    .loading { display: flex; justify-content: center; padding: 48px; }
    .progress-section { max-width: 480px; }
    .status-text {
      font-size: 14px;
      color: rgba(0, 0, 0, 0.7);
      margin-bottom: 8px;
    }
    .percent-text {
      font-size: 13px;
      color: rgba(0, 0, 0, 0.5);
      margin-top: 4px;
    }
    .status-section {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 12px;
      padding: 24px;
    }
    .status-icon { font-size: 48px; width: 48px; height: 48px; }
    .status-icon.error { color: #f44336; }
    .error-text { color: #f44336; }
  `],
})
export class FfmpegDownloadComponent implements OnInit, OnDestroy {
  private readonly api = inject(SetupApiService);
  private readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  readonly stepComplete = output<void>();

  starting = true;
  failed = false;
  errorText = '';
  progress: DownloadProgress | null = null;
  private taskId: string | null = null;
  private pollSub: Subscription | null = null;

  get statusLabel(): string {
    if (!this.progress) return '';
    const labels: Record<string, string> = {
      pending: 'Preparing download...',
      downloading: 'Downloading FFmpeg...',
      extracting: 'Extracting...',
      verifying: 'Verifying installation...',
      complete: 'Complete',
      failed: 'Failed',
    };
    return labels[this.progress.status] || this.progress.status;
  }

  ngOnInit(): void {
    this.startDownload();
  }

  private startDownload(): void {
    this.starting = true;
    this.failed = false;
    this.api.startFfmpegDownload().subscribe({
      next: (res) => {
        this.starting = false;
        if (res.status === 'already_installed') {
          this.state.markCheckpoint('ffmpeg_installed');
          this.stepComplete.emit();
          return;
        }
        this.taskId = res.task_id;
        this.startPolling();
      },
      error: (err) => {
        this.starting = false;
        this.failed = true;
        this.errorText = err.error?.detail || 'Failed to start FFmpeg download';
      },
    });
  }

  private startPolling(): void {
    if (!this.taskId) return;
    const id = this.taskId;
    this.pollSub = interval(1500).pipe(
      switchMap(() => this.api.getFfmpegProgress(id)),
      takeWhile(
        (p) => p.status !== 'complete' && p.status !== 'failed',
        true,
      ),
    ).subscribe({
      next: (p) => {
        this.progress = p;
        if (p.status === 'complete') {
          this.state.markCheckpoint('ffmpeg_installed');
          this.stepComplete.emit();
        } else if (p.status === 'failed') {
          this.failed = true;
          this.errorText = p.error || 'FFmpeg download failed';
          this.progress = null;
        }
      },
      error: () => {
        this.snackBar.open('Lost connection to server', 'Dismiss', { duration: 5000 });
      },
    });
  }

  cancel(): void {
    this.pollSub?.unsubscribe();
    this.api.cancelFfmpegDownload().subscribe();
    this.progress = null;
    this.failed = true;
    this.errorText = 'Download cancelled';
  }

  retry(): void {
    this.failed = false;
    this.errorText = '';
    this.progress = null;
    this.startDownload();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
  }
}
