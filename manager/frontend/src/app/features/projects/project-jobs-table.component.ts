// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, Input, OnChanges, OnDestroy, SimpleChanges } from '@angular/core';
import { inject } from '@angular/core';
import { DatePipe } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription, combineLatest, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';
import { AnimationService, Animation } from '../../core/services/animation.service';

export interface JobTableRow {
  name: string;
  type: 'single' | 'tiled' | 'animation';
  status: string;
  worker: string;
  time: string;
  createdAt: string;
}

const STATUS_ICONS: Record<string, string> = {
  QUEUED: 'hourglass_empty',
  RENDERING: 'sync',
  DONE: 'check_circle',
  ERROR: 'error',
  CANCELED: 'cancel',
  ASSEMBLING: 'build',
};

function formatTime(seconds: number | null): string {
  if (seconds == null || seconds <= 0) return '--';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function isTopLevelJob(job: Job): boolean {
  return job.animation === null && job.tiled_job === null && job.animation_frame === null;
}

@Component({
  selector: 'app-project-jobs-table',
  standalone: true,
  imports: [DatePipe, MatTableModule, MatIconModule, MatProgressSpinnerModule],
  template: `
    @if (loading) {
      <mat-spinner diameter="32" />
    } @else if (rows.length === 0) {
      <p class="empty">No jobs yet. Use the form above to create a render.</p>
    } @else {
      <table mat-table [dataSource]="rows" class="full-width">
        <ng-container matColumnDef="name">
          <th mat-header-cell *matHeaderCellDef>Name</th>
          <td mat-cell *matCellDef="let r">{{ r.name }}</td>
        </ng-container>
        <ng-container matColumnDef="type">
          <th mat-header-cell *matHeaderCellDef>Type</th>
          <td mat-cell *matCellDef="let r">
            <mat-icon class="type-icon">{{ typeIcon(r.type) }}</mat-icon>
            {{ typeLabel(r.type) }}
          </td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let r" [class]="'status-' + r.status">
            <mat-icon class="status-icon">{{ statusIcon(r.status) }}</mat-icon>
            {{ r.status }}
          </td>
        </ng-container>
        <ng-container matColumnDef="worker">
          <th mat-header-cell *matHeaderCellDef>Worker</th>
          <td mat-cell *matCellDef="let r">{{ r.worker }}</td>
        </ng-container>
        <ng-container matColumnDef="time">
          <th mat-header-cell *matHeaderCellDef>Time</th>
          <td mat-cell *matCellDef="let r">{{ r.time }}</td>
        </ng-container>
        <ng-container matColumnDef="createdAt">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let r">{{ r.createdAt | date:'mediumDate' }}</td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="columns"></tr>
        <tr mat-row *matRowDef="let row; columns: columns"></tr>
      </table>
      <div class="table-footer">
        <span>Showing {{ rows.length }} items</span>
        <span class="refresh-indicator"><mat-icon>sync</mat-icon> Auto-refreshing</span>
      </div>
    }
  `,
  styles: [`
    .full-width { width: 100%; }
    .empty { color: rgba(0,0,0,0.6); text-align: center; padding: 24px; }
    .type-icon, .status-icon { font-size: 18px; vertical-align: middle; margin-right: 4px; }
    .status-DONE { color: #2e7d32; }
    .status-ERROR { color: #c62828; }
    .status-RENDERING { color: #1565c0; }
    .status-QUEUED { color: #9e9e9e; }
    .table-footer {
      display: flex; justify-content: space-between; padding: 8px 0;
      font-size: 13px; color: rgba(0,0,0,0.6);
    }
    .refresh-indicator { display: flex; align-items: center; gap: 4px; }
    .refresh-indicator mat-icon { font-size: 16px; width: 16px; height: 16px; }
  `],
})
export class ProjectJobsTableComponent implements OnChanges, OnDestroy {
  @Input() projectId = '';

  private readonly jobService = inject(JobService);
  private readonly tiledJobService = inject(TiledJobService);
  private readonly animationService = inject(AnimationService);
  private sub?: Subscription;

  rows: JobTableRow[] = [];
  loading = true;
  columns = ['name', 'type', 'status', 'worker', 'time', 'createdAt'];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['projectId'] && this.projectId) this.startPolling();
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  refresh(): void { /* polling handles it */ }

  typeIcon(type: string): string {
    return type === 'tiled' ? 'grid_view' : type === 'animation' ? 'movie' : 'image';
  }

  typeLabel(type: string): string {
    return type === 'tiled' ? 'Tiled' : type === 'animation' ? 'Animation' : 'Single';
  }

  statusIcon(status: string): string {
    return STATUS_ICONS[status] || 'help_outline';
  }

  private startPolling(): void {
    this.sub?.unsubscribe();
    this.loading = true;

    const jobs$ = this.jobService.pollList({ asset__project: this.projectId })
      .pipe(catchError(() => of([] as Job[])));
    const tiled$ = this.tiledJobService.pollList({ project: this.projectId })
      .pipe(catchError(() => of([] as TiledJob[])));
    const anims$ = this.animationService.pollList({ project: this.projectId })
      .pipe(catchError(() => of([] as Animation[])));

    this.sub = combineLatest([jobs$, tiled$, anims$]).subscribe({
      next: ([jobs, tiled, anims]) => {
        this.rows = [
          ...jobs.filter(isTopLevelJob).map(j => this.mapJob(j)),
          ...tiled.map(t => this.mapTiled(t)),
          ...anims.map(a => this.mapAnim(a)),
        ].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
        this.loading = false;
      },
      error: () => { this.loading = false; },
    });
  }

  private mapJob(j: Job): JobTableRow {
    return {
      name: j.name, type: 'single', status: j.status,
      worker: j.assigned_worker_hostname || '--',
      time: formatTime(j.render_time_seconds), createdAt: j.submitted_at,
    };
  }

  private mapTiled(t: TiledJob): JobTableRow {
    return {
      name: t.name, type: 'tiled', status: t.status, worker: '--',
      time: formatTime(t.total_render_time_seconds), createdAt: t.submitted_at,
    };
  }

  private mapAnim(a: Animation): JobTableRow {
    return {
      name: a.name, type: 'animation', status: a.status, worker: '--',
      time: formatTime(a.total_render_time_seconds), createdAt: a.submitted_at,
    };
  }
}
