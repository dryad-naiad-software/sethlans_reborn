// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnInit, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatDividerModule } from '@angular/material/divider';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import {
  SupportedVersionService,
  SupportedVersion,
  DeletePreview,
} from '../../core/services/supported-version.service';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { ShutdownService } from '../../core/services/shutdown.service';
import { QueueSettingService } from '../../core/services/queue-setting.service';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../shared/confirm-dialog.component';
import { AddVersionFormComponent } from './add-version-form.component';
import { VersionTableComponent } from './version-table.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    MatButtonModule,
    MatDividerModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatDialogModule,
    AddVersionFormComponent,
    VersionTableComponent,
  ],
  template: `
    <h1>Settings</h1>

    <h2>Supported Blender Versions</h2>

    <app-add-version-form
      [creating]="creating"
      [availableSeries]="availableSeries"
      [cacheReady]="cacheReady"
      (addVersion)="onAddVersion($event)" />

    @if (loading) {
      <div class="loading">
        <mat-spinner diameter="40" />
      </div>
    } @else {
      <app-version-table
        [versions]="versions"
        [expandedDeleteId]="expandedDeleteId"
        [deletePreview]="deletePreview"
        [deletingId]="deletingId"
        [settingDefaultId]="settingDefaultId"
        (setDefault)="onSetDefault($event)"
        (requestDelete)="onRequestDelete($event)"
        (confirmDelete)="onConfirmDelete($event)"
        (cancelDelete)="onCancelDelete()" />
    }

    <mat-divider class="shutdown-divider" />

    <h2>Job Queue</h2>
    <p class="shutdown-description">Control whether workers can claim new jobs.</p>
    <span class="queue-badge" [class.paused]="queuePaused" [class.active]="!queuePaused">{{ queuePaused ? 'Paused' : 'Active' }}</span>
    <button mat-raised-button [color]="queuePaused ? 'primary' : 'warn'" (click)="toggleQueue()">
      <mat-icon>{{ queuePaused ? 'play_arrow' : 'pause' }}</mat-icon>
      {{ queuePaused ? 'Resume Queue' : 'Pause Queue' }}
    </button>
    <mat-divider class="shutdown-divider" />

    <h2>System</h2>
    <p class="shutdown-description">Shut down the Sethlans manager process. This cannot be undone from the UI.</p>
    <button mat-flat-button color="warn" [disabled]="shuttingDown" (click)="onShutdown()">
      <mat-icon>power_settings_new</mat-icon>
      {{ shuttingDown ? 'Shutting down...' : 'Shut Down Manager' }}
    </button>
  `,
  styles: [`
    h2 { margin-top: 24px; }
    .loading { display: flex; justify-content: center; padding: 48px; }
    app-add-version-form { display: block; margin-bottom: 24px; }
    .shutdown-divider { margin-top: 32px; }
    .shutdown-description { color: rgba(0, 0, 0, 0.6); margin-bottom: 16px; }
    .queue-badge { font-size: 14px; font-weight: 500; padding: 4px 12px; border-radius: 4px; margin-right: 12px; }
    .queue-badge.active { color: #2e7d32; background: #e8f5e9; }
    .queue-badge.paused { color: #e65100; background: #fff3e0; }
  `],
})
export class SettingsComponent implements OnInit {
  private readonly service = inject(SupportedVersionService);
  private readonly shutdownService = inject(ShutdownService);
  private readonly queueSettingService = inject(QueueSettingService);
  private readonly dialog = inject(MatDialog);
  private readonly snackBar = inject(MatSnackBar);

  versions: SupportedVersion[] = [];
  loading = true;
  creating = false;
  expandedDeleteId: number | null = null;
  deletePreview: DeletePreview | null = null;
  deletingId: number | null = null;
  settingDefaultId: number | null = null;
  availableSeries: string[] = [];
  cacheReady = false;
  queuePaused = false;
  shuttingDown = false;

  ngOnInit(): void {
    this.loadVersions();
    this.loadAvailableSeries();
    this.loadQueueStatus();
  }

  loadVersions(): void {
    this.loading = true;
    this.service.list().subscribe({
      next: (data) => { this.versions = data; this.loading = false; },
      error: () => {
        this.loading = false;
        this.snackBar.open('Failed to load versions', 'Dismiss', { duration: 5000 });
      },
    });
  }

  loadAvailableSeries(): void {
    this.service.availableSeries().subscribe({
      next: (data) => {
        this.availableSeries = data.series;
        this.cacheReady = data.cache_ready;
      },
      error: () => {
        this.cacheReady = true;
        this.snackBar.open(
          'Failed to load available versions', 'Dismiss', { duration: 5000 },
        );
      },
    });
  }

  onAddVersion(event: { series: string; isDefault: boolean }): void {
    this.creating = true;
    this.service.create(event.series, event.isDefault).subscribe({
      next: () => {
        this.creating = false;
        this.snackBar.open('Version added', 'Dismiss', { duration: 3000 });
        this.loadVersions();
        this.loadAvailableSeries();
      },
      error: (err) => {
        this.creating = false;
        const msg = err.error?.series?.[0] || err.error?.detail || 'Failed to add version';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }

  onSetDefault(id: number): void {
    this.settingDefaultId = id;
    this.service.setDefault(id).subscribe({
      next: () => {
        this.settingDefaultId = null;
        this.loadVersions();
      },
      error: () => {
        this.settingDefaultId = null;
        this.snackBar.open('Failed to set default', 'Dismiss', { duration: 5000 });
      },
    });
  }

  onRequestDelete(id: number): void {
    this.service.previewDelete(id).subscribe({
      next: (preview) => {
        this.expandedDeleteId = id;
        this.deletePreview = preview;
      },
      error: (err) => {
        const msg = err.error?.detail || 'Failed to preview deletion';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }

  onConfirmDelete(id: number): void {
    this.deletingId = id;
    this.service.confirmDelete(id).subscribe({
      next: () => {
        this.deletingId = null;
        this.expandedDeleteId = null;
        this.deletePreview = null;
        this.snackBar.open('Version removed', 'Dismiss', { duration: 3000 });
        this.loadVersions();
        this.loadAvailableSeries();
      },
      error: (err) => {
        this.deletingId = null;
        const msg = err.error?.detail || 'Failed to remove version';
        this.snackBar.open(msg, 'Dismiss', { duration: 5000 });
      },
    });
  }

  onCancelDelete(): void {
    this.expandedDeleteId = null;
    this.deletePreview = null;
  }

  loadQueueStatus(): void {
    this.queueSettingService.getStatus().subscribe({ next: (s) => { this.queuePaused = s.queue_paused; } });
  }

  toggleQueue(): void {
    const action = this.queuePaused ? this.queueSettingService.resume() : this.queueSettingService.pause();
    const msg = this.queuePaused ? 'Queue resumed' : 'Queue paused';
    action.subscribe({
      next: (s) => { this.queuePaused = s.queue_paused; this.snackBar.open(msg, 'Dismiss', { duration: 3000 }); },
      error: () => this.snackBar.open('Failed to update queue', 'Dismiss', { duration: 5000 }),
    });
  }

  onShutdown(): void {
    const data: ConfirmDialogData = {
      title: 'Shut Down Manager',
      message: 'Are you sure? The manager will stop and this page will become unreachable.',
      confirmText: 'Shut Down',
    };
    this.dialog.open(ConfirmDialogComponent, { data }).afterClosed().subscribe(confirmed => {
      if (!confirmed) return;
      this.shuttingDown = true;
      this.shutdownService.shutdown().subscribe({
        next: () => this.snackBar.open('Manager is shutting down...', 'Dismiss', { duration: 10000 }),
        error: () => { this.shuttingDown = false; this.snackBar.open('Failed to shut down', 'Dismiss', { duration: 5000 }); },
      });
    });
  }
}
