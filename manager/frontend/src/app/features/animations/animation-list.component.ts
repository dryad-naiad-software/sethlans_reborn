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
import { AnimationService, Animation } from '../../core/services/animation.service';

@Component({
  selector: 'app-animation-list',
  standalone: true,
  imports: [
    CommonModule, RouterLink, MatTableModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule,
  ],
  template: `
    <div class="header">
      <h1>Animations</h1>
      <button mat-raised-button color="primary">
        <mat-icon>add</mat-icon> Create Animation
      </button>
    </div>

    @if (loading) {
      <mat-spinner diameter="40" />
    } @else {
      <table mat-table [dataSource]="animations" class="full-width">
        <ng-container matColumnDef="id">
          <th mat-header-cell *matHeaderCellDef>ID</th>
          <td mat-cell *matCellDef="let a">
            <a [routerLink]="['/animations', a.id]">{{ a.id }}</a>
          </td>
        </ng-container>
        <ng-container matColumnDef="project">
          <th mat-header-cell *matHeaderCellDef>Project</th>
          <td mat-cell *matCellDef="let a">{{ a.project }}</td>
        </ng-container>
        <ng-container matColumnDef="frames">
          <th mat-header-cell *matHeaderCellDef>Frames</th>
          <td mat-cell *matCellDef="let a">{{ a.start_frame }} - {{ a.end_frame }}</td>
        </ng-container>
        <ng-container matColumnDef="status">
          <th mat-header-cell *matHeaderCellDef>Status</th>
          <td mat-cell *matCellDef="let a">{{ a.status }}</td>
        </ng-container>
        <ng-container matColumnDef="progress">
          <th mat-header-cell *matHeaderCellDef>Progress</th>
          <td mat-cell *matCellDef="let a">{{ a.progress }}%</td>
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
export class AnimationListComponent implements OnInit, OnDestroy {
  private readonly animationService = inject(AnimationService);
  private sub?: Subscription;

  animations: Animation[] = [];
  loading = true;
  displayedColumns = ['id', 'project', 'frames', 'status', 'progress'];

  ngOnInit(): void {
    this.sub = this.animationService.pollList().subscribe({
      next: (anims) => { this.animations = anims; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
