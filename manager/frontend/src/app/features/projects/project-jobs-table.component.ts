// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import {
  Component, EventEmitter, Input, OnChanges, OnDestroy,
  Output, SimpleChanges, inject,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { MatTableModule } from '@angular/material/table';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatDialog } from '@angular/material/dialog';
import { Subscription, Subject, combineLatest, of, merge, interval, startWith, switchMap } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../../environments/environment';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';
import { AnimationService, Animation } from '../../core/services/animation.service';
import { ProjectJobActionsComponent } from './project-job-actions.component';
import { JobResultDialogComponent, JobResultDialogData } from './job-result-dialog.component';
import {
  JobTableRow, STATUS_ICONS, isTopLevelJob,
  mapJobToRow, mapTiledJobToRow, mapAnimationToRow, progressPercent,
} from './project-jobs-table.util';
import { JobPrefillData } from './job-create-form.types';

export { JobTableRow } from './project-jobs-table.util';

@Component({
  selector: 'app-project-jobs-table',
  standalone: true,
  imports: [
    DatePipe, MatTableModule, MatIconModule, MatProgressSpinnerModule, MatProgressBarModule,
    ProjectJobActionsComponent,
  ],
  template: `
    @if (loading) {
      <mat-spinner diameter="32" />
    } @else if (rows.length === 0) {
      <p class="empty">No jobs yet. Click "Create Job" to get started.</p>
    } @else {
      <table mat-table [dataSource]="rows" class="full-width">
        <ng-container matColumnDef="thumbnail">
          <th mat-header-cell *matHeaderCellDef></th>
          <td mat-cell *matCellDef="let r">
            @if (r.status === 'DONE' && r.thumbnail && !r.thumbError) {
              <img [src]="r.thumbnail" width="48" height="48" class="thumb-img"
                   (error)="r.thumbError = true" (click)="openResult(r)"
                   [alt]="r.name" />
            } @else {
              <div class="thumb-placeholder"><mat-icon>image</mat-icon></div>
            }
          </td>
        </ng-container>
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
        <ng-container matColumnDef="progress">
          <th mat-header-cell *matHeaderCellDef>Progress</th>
          <td mat-cell *matCellDef="let r">
            @if (r.total == null) {
              <span class="progress-empty">--</span>
            } @else {
              <div class="progress-cell">
                <mat-progress-bar mode="determinate" [value]="progressPercent(r)" />
                <span class="progress-text">{{ r.completed }} / {{ r.total }} {{ r.progressUnit }}</span>
              </div>
            }
          </td>
        </ng-container>
        <ng-container matColumnDef="time">
          <th mat-header-cell *matHeaderCellDef>Time</th>
          <td mat-cell *matCellDef="let r">{{ r.time }}</td>
        </ng-container>
        <ng-container matColumnDef="createdAt">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let r">{{ r.createdAt | date:'mediumDate' }}</td>
        </ng-container>
        <ng-container matColumnDef="actions">
          <th mat-header-cell *matHeaderCellDef>Actions</th>
          <td mat-cell *matCellDef="let r">
            <app-project-job-actions [row]="r"
              (canceled)="triggerRefresh()" (requeued)="triggerRefresh()"
              (deleted)="triggerRefresh()" (paused)="triggerRefresh()"
              (unpaused)="triggerRefresh()" (viewResult)="openResult($event)" />
          </td>
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
    .status-PAUSED { color: #e65100; }
    .thumb-img {
      width: 48px; height: 48px; object-fit: cover; border-radius: 4px; cursor: pointer;
    }
    .thumb-placeholder {
      width: 48px; height: 48px; display: flex; align-items: center; justify-content: center;
    }
    .thumb-placeholder mat-icon { color: rgba(0,0,0,0.3); }
    .progress-cell { display: flex; flex-direction: column; gap: 2px; min-width: 120px; }
    .progress-cell mat-progress-bar { height: 4px; border-radius: 2px; }
    .progress-text { font-size: 12px; color: rgba(0,0,0,0.6); }
    .progress-empty { color: rgba(0,0,0,0.4); }
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
  @Output() activeJobCount = new EventEmitter<number>();
  @Output() rerender = new EventEmitter<JobPrefillData>();

  private readonly jobService = inject(JobService);
  private readonly tiledJobService = inject(TiledJobService);
  private readonly animationService = inject(AnimationService);
  private readonly dialog = inject(MatDialog);
  private readonly refresh$ = new Subject<void>();
  private sub?: Subscription;

  private jobs: Job[] = [];
  private tiledJobs: TiledJob[] = [];
  private animList: Animation[] = [];

  rows: JobTableRow[] = [];
  loading = true;
  columns = ['thumbnail', 'name', 'type', 'status', 'progress', 'time', 'createdAt', 'actions'];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['projectId'] && this.projectId) this.startPolling();
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  triggerRefresh(): void { this.refresh$.next(); }

  typeIcon(type: string): string {
    return type === 'tiled' ? 'grid_view' : type === 'animation' ? 'movie' : 'image';
  }

  typeLabel(type: string): string {
    return type === 'tiled' ? 'Tiled' : type === 'animation' ? 'Animation' : 'Single';
  }

  statusIcon(status: string): string { return STATUS_ICONS[status] || 'help_outline'; }

  progressPercent(row: JobTableRow): number { return progressPercent(row); }

  openResult(row: JobTableRow): void {
    if (row.status !== 'DONE') return;
    const dialogData = this.buildDialogData(row);
    if (!dialogData) return;
    this.dialog.open(JobResultDialogComponent, {
      width: '800px', maxWidth: '95vw', data: dialogData,
    }).afterClosed().subscribe(result => {
      if (result?.action === 'rerender') this.rerender.emit(result.prefill);
    });
  }

  private buildDialogData(row: JobTableRow): JobResultDialogData | null {
    if (row.type === 'single') {
      const job = this.jobs.find(j => j.id === row.id);
      return job ? { type: 'single', job } : null;
    }
    if (row.type === 'tiled') {
      const tj = this.tiledJobs.find(t => t.id === row.id);
      return tj ? { type: 'tiled', tiledJob: tj } : null;
    }
    const anim = this.animList.find(a => a.id === row.id);
    return anim ? { type: 'animation', animation: anim } : null;
  }

  private startPolling(): void {
    this.sub?.unsubscribe();
    this.loading = true;
    const tick$ = merge(this.refresh$, interval(environment.pollingIntervalMs)).pipe(startWith(0));
    this.sub = tick$.pipe(
      switchMap(() => {
        const jobs$ = this.jobService.list({ asset__project: this.projectId })
          .pipe(catchError(() => of([] as Job[])));
        const tiled$ = this.tiledJobService.list({ project: this.projectId })
          .pipe(catchError(() => of([] as TiledJob[])));
        const anims$ = this.animationService.list({ project: this.projectId })
          .pipe(catchError(() => of([] as Animation[])));
        return combineLatest([jobs$, tiled$, anims$]);
      }),
    ).subscribe({
      next: ([jobs, tiled, anims]) => {
        this.jobs = jobs;
        this.tiledJobs = tiled;
        this.animList = anims;
        this.rows = [
          ...jobs.filter(isTopLevelJob).map(mapJobToRow),
          ...tiled.map(mapTiledJobToRow),
          ...anims.map(mapAnimationToRow),
        ].sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
        this.activeJobCount.emit(
          this.rows.filter(r => r.status === 'QUEUED' || r.status === 'RENDERING').length,
        );
        this.loading = false;
      },
      error: () => { this.loading = false; },
    });
  }
}
