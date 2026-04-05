// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { Subscription, switchMap, filter } from 'rxjs';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService, Asset } from '../../core/services/asset.service';
import { Animation } from '../../core/services/animation.service';
import { poll } from '../../core/services/polling.util';
import { JobCreateFormComponent } from './job-create-form.component';
import { JobCreateDialogData, JobPrefillData } from './job-create-form.types';
import { ProjectJobsTableComponent } from './project-jobs-table.component';
import { ConfirmDialogComponent, ConfirmDialogData } from '../../shared/confirm-dialog.component';
import {
  AnimationFramesSectionComponent, FrameClickEvent,
} from './animation-frames-section.component';
import { JobResultDialogComponent } from './job-result-dialog.component';

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [
    DatePipe, RouterLink, MatButtonModule, MatIconModule,
    MatDividerModule, MatProgressSpinnerModule, MatSnackBarModule,
    MatDialogModule, ProjectJobsTableComponent, AnimationFramesSectionComponent,
  ],
  template: `
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (project) {
      <a mat-button routerLink="/projects"><mat-icon>arrow_back</mat-icon> Back</a>
      <div class="header">
        <div>
          <h1>{{ project.name }}</h1>
          <p class="subtitle">
            Blender {{ project.blender_version_details.series }}
            ({{ project.blender_version_details.resolved_version }})
            @if (asset) { &middot; {{ asset.name }} }
            &middot; Created {{ project.created_at | date:'mediumDate' }}
          </p>
          <p class="status-line">
            Status:
            <span [class]="project.is_paused ? 'paused' : 'active'">
              {{ project.is_paused ? 'Paused' : 'Active' }}
            </span>
          </p>
        </div>
        <div class="header-actions">
          <button mat-raised-button (click)="togglePause()">
            <mat-icon>{{ project.is_paused ? 'play_arrow' : 'pause' }}</mat-icon>
            {{ project.is_paused ? 'Unpause' : 'Pause' }}
          </button>
          <button mat-raised-button color="warn" (click)="showDeleteConfirm = true"
                  [disabled]="showDeleteConfirm">
            <mat-icon>delete</mat-icon> Delete
          </button>
        </div>
      </div>
      @if (showDeleteConfirm) {
        <div class="delete-confirm">
          <mat-icon color="warn">warning</mat-icon>
          <span>Are you sure? This will delete the project, all assets, and all jobs permanently.</span>
          <button mat-button (click)="showDeleteConfirm = false">Cancel</button>
          <button mat-flat-button color="warn" (click)="deleteProject()" [disabled]="deleting">
            @if (deleting) { Deleting... } @else { Confirm Delete }
          </button>
        </div>
      }
      <mat-divider />
      <div class="jobs-header">
        <h2>Jobs</h2>
        <div class="jobs-actions">
          <button mat-raised-button color="warn" (click)="cancelAllJobs()"
                  [disabled]="activeJobCount === 0">
            <mat-icon>cancel</mat-icon> Cancel All
          </button>
          <button mat-raised-button color="primary" (click)="openCreateRender()"
                  [disabled]="!asset">
            <mat-icon>add</mat-icon> Create Job
          </button>
        </div>
      </div>
      <app-project-jobs-table #jobsTable [projectId]="project.id"
        (activeJobCount)="activeJobCount = $event"
        (animations)="doneAnimations = filterDone($event)"
        (rerender)="openCreateRender($event)" />
      @for (anim of doneAnimations; track anim.id) {
        <app-animation-frames-section [animation]="anim"
          (frameClick)="onFrameClick($event)" />
      }
    } @else {
      <p>Project not found.</p>
    }
  `,
  styles: [`
    .header { display: flex; justify-content: space-between; align-items: flex-start; }
    h1 { margin-bottom: 4px; }
    .subtitle { color: rgba(0,0,0,0.6); margin: 0 0 4px; }
    .status-line { margin: 0; }
    .active { color: #2e7d32; font-weight: 500; }
    .paused { color: #e65100; font-weight: 500; }
    .header-actions { display: flex; gap: 8px; align-items: center; }
    .delete-confirm {
      display: flex; align-items: center; gap: 8px; padding: 12px;
      background: #fff3e0; border-radius: 4px; margin: 12px 0;
    }
    mat-divider { margin: 16px 0; }
    .jobs-header {
      display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;
    }
    .jobs-header h2 { margin: 0; }
    .jobs-actions { display: flex; gap: 8px; }
  `],
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
  @ViewChild('jobsTable') jobsTable?: ProjectJobsTableComponent;

  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly projectService = inject(ProjectService);
  private readonly assetService = inject(AssetService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly dialog = inject(MatDialog);
  private projectSub?: Subscription;
  private assetSub?: Subscription;

  project: Project | null = null;
  asset: Asset | null = null;
  loading = true;
  showDeleteConfirm = false;
  deleting = false;
  activeJobCount = 0;
  doneAnimations: Animation[] = [];

  ngOnInit(): void {
    this.projectSub = this.route.paramMap.pipe(
      switchMap(params => {
        const id = params.get('id')!;
        this.fetchAsset(id);
        return poll(() => this.projectService.get(id));
      }),
    ).subscribe({
      next: (p) => { this.project = p; this.loading = false; },
      error: () => {
        this.loading = false;
        this.snackBar.open('Failed to load project', 'Dismiss', { duration: 5000 });
      },
    });
  }

  ngOnDestroy(): void {
    this.projectSub?.unsubscribe();
    this.assetSub?.unsubscribe();
  }

  filterDone(anims: Animation[]): Animation[] {
    return anims.filter(a => a.status === 'DONE');
  }

  togglePause(): void {
    if (!this.project) return;
    const action = this.project.is_paused
      ? this.projectService.unpause(this.project.id)
      : this.projectService.pause(this.project.id);
    action.subscribe({
      next: (p) => { this.project = p; },
      error: () => this.snackBar.open('Failed to update project', 'Dismiss', { duration: 5000 }),
    });
  }

  deleteProject(): void {
    if (!this.project) return;
    this.deleting = true;
    this.projectService.delete(this.project.id).subscribe({
      next: () => {
        this.snackBar.open('Project deleted', 'Dismiss', { duration: 3000 });
        this.router.navigate(['/projects']);
      },
      error: () => {
        this.deleting = false;
        this.snackBar.open('Failed to delete project', 'Dismiss', { duration: 5000 });
      },
    });
  }

  cancelAllJobs(): void {
    if (!this.project) return;
    const data: ConfirmDialogData = {
      title: 'Cancel All Jobs',
      message: 'Cancel all queued and in-progress jobs for this project?',
    };
    this.dialog.open(ConfirmDialogComponent, { data }).afterClosed().pipe(
      filter((confirmed: boolean) => confirmed === true),
      switchMap(() => this.projectService.cancelAllJobs(this.project!.id)),
    ).subscribe({
      next: (res) => {
        this.snackBar.open(`Canceled ${res.canceled} jobs`, 'Dismiss', { duration: 3000 });
        this.jobsTable?.triggerRefresh();
      },
      error: () => this.snackBar.open('Failed to cancel jobs', 'Dismiss', { duration: 5000 }),
    });
  }

  openCreateRender(prefill?: JobPrefillData): void {
    if (!this.project || !this.asset) return;
    const data: JobCreateDialogData = {
      projectId: this.project.id, assetId: this.asset.id,
      ...(prefill ? { prefill } : {}),
    };
    this.dialog.open(JobCreateFormComponent, { width: '700px', data });
  }

  onFrameClick(event: FrameClickEvent): void {
    this.dialog.open(JobResultDialogComponent, {
      width: '800px', maxWidth: '95vw',
      data: { type: 'animation' as const, animation: event.animation,
              selectedFrameIndex: event.frameIndex },
    });
  }

  private fetchAsset(projectId: string): void {
    this.assetSub?.unsubscribe();
    this.assetSub = this.assetService.list({ project: projectId }).subscribe({
      next: (assets) => { this.asset = assets.length > 0 ? assets[0] : null; },
      error: () => {
        this.snackBar.open('Failed to load asset', 'Dismiss', { duration: 5000 });
      },
    });
  }
}
