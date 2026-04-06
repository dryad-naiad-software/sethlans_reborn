// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { DatePipe } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { Subscription } from 'rxjs';
import { ProjectService, Project } from '../../core/services/project.service';
import { CreateProjectDialogComponent } from './create-project-dialog.component';

@Component({
  selector: 'app-project-list',
  standalone: true,
  imports: [
    DatePipe, RouterLink, MatTableModule, MatButtonModule,
    MatIconModule, MatProgressSpinnerModule, MatSnackBarModule, MatDialogModule,
  ],
  template: `
    <div class="header">
      <h1>Projects</h1>
      <button mat-raised-button color="primary" (click)="openCreateDialog()">
        <mat-icon>add</mat-icon> New Project
      </button>
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (projects.length === 0) {
      <p class="empty-state">No projects yet. Click 'New Project' to get started.</p>
    } @else {
      <table mat-table [dataSource]="projects" class="full-width">
        <ng-container matColumnDef="name">
          <th mat-header-cell *matHeaderCellDef>Name</th>
          <td mat-cell *matCellDef="let p">
            <a [routerLink]="['/projects', p.id]">{{ p.name }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="blender_version">
          <th mat-header-cell *matHeaderCellDef>Blender</th>
          <td mat-cell *matCellDef="let p">
            {{ p.blender_version_details.series }}
            ({{ p.blender_version_details.resolved_version }})
          </td>
        </ng-container>
        <ng-container matColumnDef="created_at">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let p">{{ p.created_at | date:'mediumDate' }}</td>
        </ng-container>
        <ng-container matColumnDef="actions">
          <th mat-header-cell *matHeaderCellDef>Actions</th>
          <td mat-cell *matCellDef="let p">
            @if (confirmDeleteId === p.id) {
              <button mat-button (click)="confirmDeleteId = null">Cancel</button>
              <button mat-flat-button color="warn" (click)="deleteProject(p.id)">
                Confirm
              </button>
            } @else {
              <button mat-icon-button color="warn"
                      (click)="confirmDeleteId = p.id"
                      [disabled]="confirmDeleteId !== null"
                      title="Delete">
                <mat-icon>delete</mat-icon>
              </button>
            }
          </td>
        </ng-container>
        <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
        <tr mat-row *matRowDef="let row; columns: displayedColumns"></tr>
      </table>
    }
  `,
  styles: [`
    .header { display: flex; justify-content: space-between; align-items: center; }
    .full-width { width: 100%; }
    .empty-state { color: rgba(0,0,0,0.6); text-align: center; padding: 48px 0; }
  `],
})
export class ProjectListComponent implements OnInit, OnDestroy {
  private readonly projectService = inject(ProjectService);
  private readonly dialog = inject(MatDialog);
  private readonly router = inject(Router);
  private readonly snackBar = inject(MatSnackBar);
  private sub?: Subscription;

  projects: Project[] = [];
  loading = true;
  confirmDeleteId: string | null = null;
  displayedColumns = ['name', 'blender_version', 'created_at', 'actions'];

  ngOnInit(): void {
    this.sub = this.projectService.pollList().subscribe({
      next: (projects) => { this.projects = projects; this.loading = false; },
      error: () => {
        this.loading = false;
        this.snackBar.open('Failed to load projects', 'Dismiss', { duration: 5000 });
      },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  openCreateDialog(): void {
    const ref = this.dialog.open(CreateProjectDialogComponent, {
      disableClose: true,
      width: '500px',
    });
    ref.afterClosed().subscribe((project: Project | undefined) => {
      if (project) {
        this.router.navigate(['/projects', project.id]);
      }
    });
  }

  deleteProject(id: string): void {
    this.confirmDeleteId = null;
    this.projectService.delete(id).subscribe({
      next: () => this.snackBar.open('Project deleted', 'Dismiss', { duration: 3000 }),
      error: () => this.snackBar.open('Failed to delete project', 'Dismiss', { duration: 5000 }),
    });
  }
}
