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
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';

@Component({
  selector: 'app-tiled-job-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, MatTableModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="header">
      <h1>Tiled Jobs</h1>
      <button mat-raised-button color="primary">
        <mat-icon>add</mat-icon> Create Tiled Job
      </button>
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="tiledJobs" class="full-width">
        <ng-container matColumnDef="id">
          <th mat-header-cell *matHeaderCellDef>ID</th>
          <td mat-cell *matCellDef="let t">
            <a [routerLink]="['/tiled-jobs', t.id]">{{ t.id }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="project">
          <th mat-header-cell *matHeaderCellDef>Project</th>
          <td mat-cell *matCellDef="let t">{{ t.project }}</td>
        </ng-container>
        <ng-container matColumnDef="tiling">
          <th mat-header-cell *matHeaderCellDef>Tiling</th>
          <td mat-cell *matCellDef="let t">{{ t.tile_count_x }}x{{ t.tile_count_y }}</td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let t">{{ t.status }}</td>
        </ng-container>
        <ng-container matColumnDef="progress">
          <th mat-header-cell *matHeaderCellDef>Progress</th>
          <td mat-cell *matCellDef="let t">{{ t.progress }}%</td>
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
export class TiledJobListComponent implements OnInit, OnDestroy {
  private readonly tiledJobService = inject(TiledJobService);
  private sub?: Subscription;

  tiledJobs: TiledJob[] = [];
  loading = true;
  displayedColumns = ['id', 'project', 'tiling', 'status', 'progress'];

  ngOnInit(): void {
    this.sub = this.tiledJobService.pollList().subscribe({
      next: (jobs) => { this.tiledJobs = jobs; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
