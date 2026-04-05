// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatSelectModule } from '@angular/material/select';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subscription, Subject, switchMap, startWith } from 'rxjs';
import { JobService, Job, JobFilter } from '../../core/services/job.service';
import { QueueSettingService, QueueStatus } from '../../core/services/queue-setting.service';

@Component({
  selector: 'app-job-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, FormsModule, MatTableModule,
    MatSelectModule, MatFormFieldModule, MatProgressSpinnerModule,
    MatButtonModule, MatIconModule, MatSnackBarModule,
  ],
  template: `
    <div class="page-header">
      <h1>Jobs</h1>
      <div class="queue-controls">
        @if (queuePaused) {
          <button mat-raised-button color="primary" (click)="resumeQueue()">
            <mat-icon>play_arrow</mat-icon> Resume Queue
          </button>
        } @else {
          <button mat-raised-button color="warn" (click)="pauseQueue()">
            <mat-icon>pause</mat-icon> Pause Queue
          </button>
        }
      </div>
    </div>

    <div class="queue-status" [class.paused]="queuePaused" [class.active]="!queuePaused">
      Queue: {{ queuePaused ? 'Paused' : 'Active' }}
    </div>

    <div class="filters">
      <mat-form-field>
        <mat-label>Status</mat-label>
        <mat-select [(value)]="statusFilter" (selectionChange)="applyFilter()">
          <mat-option value="">All</mat-option>
          <mat-option value="QUEUED">Queued</mat-option>
          <mat-option value="RENDERING">Rendering</mat-option>
          <mat-option value="DONE">Done</mat-option>
          <mat-option value="ERROR">Error</mat-option>
          <mat-option value="CANCELED">Canceled</mat-option>
        </mat-select>
      </mat-form-field>
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="jobs" class="full-width">
        <ng-container matColumnDef="id">
          <th mat-header-cell *matHeaderCellDef>ID</th>
          <td mat-cell *matCellDef="let j">
            <a [routerLink]="['/jobs', j.id]">{{ j.id }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let j">{{ j.status }}</td>
        </ng-container>
        <ng-container matColumnDef="render_engine">
          <th mat-header-cell *matHeaderCellDef>Engine</th>
          <td mat-cell *matCellDef="let j">{{ j.render_engine }}</td>
        </ng-container>
        <ng-container matColumnDef="worker">
          <th mat-header-cell *matHeaderCellDef>Worker</th>
          <td mat-cell *matCellDef="let j">{{ j.assigned_worker_hostname ?? 'Unassigned' }}</td>
        </ng-container>
        <ng-container matColumnDef="submitted_at">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let j">{{ j.submitted_at | date }}</td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let row; columns: displayedColumns"></tr>
      </table>
    }
  `,
  styles: [`
    .page-header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 8px;
    }
    .page-header h1 { margin: 0; }
    .queue-controls { display: flex; gap: 8px; }
    .queue-status {
      font-size: 14px; font-weight: 500; margin-bottom: 16px;
      padding: 4px 12px; border-radius: 4px; display: inline-block;
    }
    .queue-status.active { color: #2e7d32; background: #e8f5e9; }
    .queue-status.paused { color: #e65100; background: #fff3e0; }
    .filters { margin-bottom: 16px; }
    .full-width { width: 100%; }
  `],
})
export class JobListComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly queueSettingService = inject(QueueSettingService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly filterChange$ = new Subject<void>();
  private sub?: Subscription;
  private queueSub?: Subscription;

  jobs: Job[] = [];
  loading = true;
  statusFilter = '';
  queuePaused = false;
  displayedColumns = ['id', 'status', 'render_engine', 'worker', 'submitted_at'];

  ngOnInit(): void {
    this.sub = this.filterChange$.pipe(
      startWith(undefined),
      switchMap(() => {
        const filters: JobFilter = {};
        if (this.statusFilter) filters.status = this.statusFilter;
        return this.jobService.pollList(filters);
      }),
    ).subscribe({
      next: (jobs) => { this.jobs = jobs; this.loading = false; },
      error: () => { this.loading = false; },
    });

    this.queueSub = this.queueSettingService.pollStatus().subscribe({
      next: (status) => { this.queuePaused = status.queue_paused; },
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
    this.queueSub?.unsubscribe();
  }

  applyFilter(): void { this.filterChange$.next(); }

  pauseQueue(): void {
    this.queueSettingService.pause().subscribe({
      next: (status) => {
        this.queuePaused = status.queue_paused;
        this.snackBar.open('Queue paused', 'Dismiss', { duration: 3000 });
      },
      error: () => this.snackBar.open('Failed to pause queue', 'Dismiss', { duration: 5000 }),
    });
  }

  resumeQueue(): void {
    this.queueSettingService.resume().subscribe({
      next: (status) => {
        this.queuePaused = status.queue_paused;
        this.snackBar.open('Queue resumed', 'Dismiss', { duration: 3000 });
      },
      error: () => this.snackBar.open('Failed to resume queue', 'Dismiss', { duration: 5000 }),
    });
  }
}
