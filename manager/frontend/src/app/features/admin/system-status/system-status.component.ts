// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, OnInit, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { MatCardModule } from '@angular/material/card';
import { MatChipsModule } from '@angular/material/chips';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { environment } from '../../../../environments/environment';
import { FFmpegStatusResponse } from '../../../core/services/ffmpeg-status.service';
import { Part } from './system-status.models';

/**
 * Admin "System status" page.
 *
 * Lists every part the manager's parts-check tracks. Today: FFmpeg.
 * Each part is rendered as a `<mat-card>` (NOT `<mat-table>`) so per-part
 * error strings have flexible vertical layout.
 *
 * Fetches `/api/ffmpeg-status/` exactly once on `ngOnInit`. No timer, no polling.
 * The user refreshes the page to pick up new state.
 */
@Component({
  selector: 'app-system-status',
  standalone: true,
  imports: [
    MatCardModule,
    MatChipsModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
  ],
  templateUrl: './system-status.component.html',
  styleUrls: ['./system-status.component.scss'],
})
export class SystemStatusComponent implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly snackBar = inject(MatSnackBar);

  readonly loading = signal(true);
  readonly parts = signal<Part[]>([]);
  readonly errorMessage = signal<string | null>(null);

  ngOnInit(): void {
    this.http
      .get<FFmpegStatusResponse>(`${environment.apiBaseUrl}/ffmpeg-status/`)
      .subscribe({
        next: resp => {
          const parts: Part[] = [];
          if (resp.ffmpeg) {
            parts.push({ name: 'FFmpeg', details: resp.ffmpeg });
          }
          this.parts.set(parts);
          this.loading.set(false);
        },
        error: () => {
          this.errorMessage.set('Unable to load system status. Refresh to retry.');
          this.loading.set(false);
          this.snackBar.open('Failed to load system status', 'Dismiss', {
            duration: 5000,
          });
        },
      });
  }

  statusColor(status: 'ready' | 'installing' | 'failed'): string {
    if (status === 'ready') return 'primary';
    if (status === 'installing') return 'accent';
    return 'warn';
  }
}
