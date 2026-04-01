// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { ProjectService, Project } from '../../core/services/project.service';

@Component({
  selector: 'app-project-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, MatTableModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="header">
      <h1>Projects</h1>
      <button mat-raised-button color="primary">
        <mat-icon>add</mat-icon> New Project
      </button>
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="projects" class="full-width">
        <ng-container matColumnDef="name">
          <th mat-header-cell *matHeaderCellDef>Name</th>
          <td mat-cell *matCellDef="let p">
            <a [routerLink]="['/projects', p.id]">{{ p.name }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let p">{{ p.status }}</td>
        </ng-container>
        <ng-container matColumnDef="created_at">
          <th mat-header-cell *matHeaderCellDef>Created</th>
          <td mat-cell *matCellDef="let p">{{ p.created_at | date }}</td>
        </ng-container>
        <ng-container matColumnDef="actions">
          <th mat-header-cell *matHeaderCellDef>Actions</th>
          <td mat-cell *matCellDef="let p">
            <button mat-icon-button (click)="togglePause(p)">
              <mat-icon>{{ p.status === 'PAUSED' ? 'play_arrow' : 'pause' }}</mat-icon>
            </button>
            <button mat-icon-button color="warn" (click)="deleteProject(p.id)">
              <mat-icon>delete</mat-icon>
            </button>
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
  `],
})
export class ProjectListComponent implements OnInit, OnDestroy {
  private readonly projectService = inject(ProjectService);
  private sub?: Subscription;

  projects: Project[] = [];
  loading = true;
  displayedColumns = ['name', 'status', 'created_at', 'actions'];

  ngOnInit(): void {
    this.sub = this.projectService.pollList().subscribe({
      next: (projects) => { this.projects = projects; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  togglePause(project: Project): void {
    const action = project.status === 'PAUSED'
      ? this.projectService.unpause(project.id)
      : this.projectService.pause(project.id);
    action.subscribe();
  }

  deleteProject(id: number): void {
    this.projectService.delete(id).subscribe();
  }
}
