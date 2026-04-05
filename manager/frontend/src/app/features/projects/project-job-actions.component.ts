// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { filter, switchMap } from 'rxjs';
import { JobService } from '../../core/services/job.service';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../shared/confirm-dialog.component';
import { JobTableRow } from './project-jobs-table.util';

@Component({
  selector: 'app-project-job-actions',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatSnackBarModule, MatDialogModule],
  template: `
    @if (row.status === 'DONE' && (row.outputFile || row.type === 'animation')) {
      <button mat-icon-button (click)="viewResult.emit(row)" aria-label="View result">
        <mat-icon>visibility</mat-icon>
      </button>
    }
    @if (row.type === 'single') {
      @if (row.status === 'QUEUED' || row.status === 'RENDERING') {
        <button mat-icon-button (click)="onCancel()" aria-label="Cancel job">
          <mat-icon>cancel</mat-icon>
        </button>
      }
      @if (row.status === 'ERROR' || row.status === 'CANCELED') {
        <button mat-icon-button (click)="onRequeue()" aria-label="Requeue job">
          <mat-icon>replay</mat-icon>
        </button>
      }
      <button mat-icon-button (click)="onDelete()" aria-label="Delete job">
        <mat-icon>delete</mat-icon>
      </button>
    }
  `,
  styles: [`
    :host { display: flex; gap: 0; align-items: center; }
    button { opacity: 0.7; }
    button:hover { opacity: 1; }
  `],
})
export class ProjectJobActionsComponent {
  @Input() row!: JobTableRow;
  @Output() canceled = new EventEmitter<void>();
  @Output() requeued = new EventEmitter<void>();
  @Output() deleted = new EventEmitter<void>();
  @Output() viewResult = new EventEmitter<JobTableRow>();

  private readonly jobService = inject(JobService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly dialog = inject(MatDialog);

  onCancel(): void {
    const jobId = this.row.id as number;
    if (this.row.status === 'RENDERING') {
      const data: ConfirmDialogData = {
        title: 'Cancel Job',
        message: 'This will stop the in-progress render. Continue?',
      };
      this.dialog.open(ConfirmDialogComponent, { data }).afterClosed().pipe(
        filter((confirmed: boolean) => confirmed === true),
        switchMap(() => this.jobService.cancel(jobId)),
      ).subscribe({
        next: () => {
          this.snackBar.open('Job canceled', 'Dismiss', { duration: 3000 });
          this.canceled.emit();
        },
        error: () => this.snackBar.open('Failed to cancel job', 'Dismiss', { duration: 5000 }),
      });
    } else {
      this.jobService.cancel(jobId).subscribe({
        next: () => {
          this.snackBar.open('Job canceled', 'Dismiss', { duration: 3000 });
          this.canceled.emit();
        },
        error: () => this.snackBar.open('Failed to cancel job', 'Dismiss', { duration: 5000 }),
      });
    }
  }

  onRequeue(): void {
    this.jobService.requeue(this.row.id as number).subscribe({
      next: () => {
        this.snackBar.open('Job requeued', 'Dismiss', { duration: 3000 });
        this.requeued.emit();
      },
      error: () => this.snackBar.open('Failed to requeue job', 'Dismiss', { duration: 5000 }),
    });
  }

  onDelete(): void {
    const message = this.row.status === 'DONE'
      ? 'This render completed successfully. Are you sure you want to delete it?'
      : 'Delete this job? This cannot be undone.';
    const data: ConfirmDialogData = { title: 'Delete Job', message };
    this.dialog.open(ConfirmDialogComponent, { data }).afterClosed().pipe(
      filter((confirmed: boolean) => confirmed === true),
      switchMap(() => this.jobService.delete(this.row.id as number)),
    ).subscribe({
      next: () => {
        this.snackBar.open('Job deleted', 'Dismiss', { duration: 3000 });
        this.deleted.emit();
      },
      error: () => this.snackBar.open('Failed to delete job', 'Dismiss', { duration: 5000 }),
    });
  }
}
