// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnInit, inject } from '@angular/core';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import {
  SupportedVersionService,
  SupportedVersion,
  DeletePreview,
} from '../../core/services/supported-version.service';
import { AddVersionFormComponent } from './add-version-form.component';
import { VersionTableComponent } from './version-table.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [
    MatProgressSpinnerModule,
    MatSnackBarModule,
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
  `,
  styles: [`
    h2 { margin-top: 24px; }
    .loading { display: flex; justify-content: center; padding: 48px; }
    app-add-version-form { display: block; margin-bottom: 24px; }
  `],
})
export class SettingsComponent implements OnInit {
  private readonly service = inject(SupportedVersionService);
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

  ngOnInit(): void {
    this.loadVersions();
    this.loadAvailableSeries();
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
}
