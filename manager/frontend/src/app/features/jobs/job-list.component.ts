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
import { Subscription, Subject, switchMap, startWith } from 'rxjs';
import { JobService, Job, JobFilter } from '../../core/services/job.service';

@Component({
  selector: 'app-job-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, FormsModule, MatTableModule,
    MatSelectModule, MatFormFieldModule, MatProgressSpinnerModule,
  ],
  template: `
    <h1>Jobs</h1>

    <div class="filters">
      <mat-form-field>
        <mat-label>Status</mat-label>
        <mat-select [(value)]="statusFilter" (selectionChange)="applyFilter()">
          <mat-option value="">All</mat-option>
          <mat-option value="QUEUED">Queued</mat-option>
          <mat-option value="RENDERING">Rendering</mat-option>
          <mat-option value="DONE">Done</mat-option>
          <mat-option value="ERROR">Error</mat-option>
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
    .filters { margin-bottom: 16px; }
    .full-width { width: 100%; }
  `],
})
export class JobListComponent implements OnInit, OnDestroy {
  private readonly jobService = inject(JobService);
  private readonly filterChange$ = new Subject<void>();
  private sub?: Subscription;

  jobs: Job[] = [];
  loading = true;
  statusFilter = '';
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
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  applyFilter(): void { this.filterChange$.next(); }
}
