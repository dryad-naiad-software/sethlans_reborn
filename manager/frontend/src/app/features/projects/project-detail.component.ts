// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy, ViewChild } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Subscription, switchMap } from 'rxjs';
import { ProjectService, Project } from '../../core/services/project.service';
import { AssetService, Asset } from '../../core/services/asset.service';
import { poll } from '../../core/services/polling.util';
import { JobCreateFormComponent } from './job-create-form.component';
import { ProjectJobsTableComponent } from './project-jobs-table.component';

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [
    DatePipe, RouterLink, MatButtonModule, MatIconModule, MatCardModule,
    MatDividerModule, MatProgressSpinnerModule, MatSnackBarModule,
    JobCreateFormComponent, ProjectJobsTableComponent,
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
          <button mat-flat-button color="warn" (click)="deleteProject()"
                  [disabled]="deleting">
            @if (deleting) { Deleting... } @else { Confirm Delete }
          </button>
        </div>
      }

      <mat-divider />

      @if (asset) {
        <section class="section">
          <h2>Asset</h2>
          <p>{{ asset.name }} &middot; Uploaded {{ asset.created_at | date:'mediumDate' }}</p>
        </section>
        <mat-divider />
      }

      <section class="section">
        <app-job-create-form
          [projectId]="project.id"
          [assetId]="asset?.id ?? 0"
          (jobCreated)="onJobCreated()" />
      </section>

      <mat-divider />

      <section class="section">
        <h2>Jobs</h2>
        <app-project-jobs-table #jobsTable [projectId]="project.id" />
      </section>
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
    .section { margin: 16px 0; }
    section h2 { margin-bottom: 8px; }
  `],
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly projectService = inject(ProjectService);
  private readonly assetService = inject(AssetService);
  private readonly snackBar = inject(MatSnackBar);
  private projectSub?: Subscription;
  private assetSub?: Subscription;

  @ViewChild('jobsTable') jobsTable?: ProjectJobsTableComponent;

  project: Project | null = null;
  asset: Asset | null = null;
  loading = true;
  showDeleteConfirm = false;
  deleting = false;

  ngOnInit(): void {
    this.projectSub = this.route.paramMap.pipe(
      switchMap(params => {
        const id = params.get('id')!;
        this.startAssetPolling(id);
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

  onJobCreated(): void {
    // Jobs table is already polling, so the new job will appear automatically
  }

  private startAssetPolling(projectId: string): void {
    this.assetSub?.unsubscribe();
    this.assetSub = this.assetService.list({ project: projectId }).subscribe({
      next: (assets) => { this.asset = assets.length > 0 ? assets[0] : null; },
      error: () => {
        this.snackBar.open('Failed to load asset', 'Dismiss', { duration: 5000 });
      },
    });
  }
}
