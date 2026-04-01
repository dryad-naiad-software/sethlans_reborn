import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { StatsService, DashboardStats } from '../../core/services/stats.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatIconModule, MatProgressSpinnerModule],
  template: `
    <h1>Dashboard</h1>
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (stats) {
      <div class="stats-grid">
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>folder</mat-icon>
            <mat-card-title>{{ stats.totalProjects }}</mat-card-title>
            <mat-card-subtitle>Projects</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>work</mat-icon>
            <mat-card-title>{{ stats.totalJobs }}</mat-card-title>
            <mat-card-subtitle>Total Jobs</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>play_circle</mat-icon>
            <mat-card-title>{{ stats.activeJobs }}</mat-card-title>
            <mat-card-subtitle>Active Jobs</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>check_circle</mat-icon>
            <mat-card-title>{{ stats.completedJobs }}</mat-card-title>
            <mat-card-subtitle>Completed</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>error</mat-icon>
            <mat-card-title>{{ stats.errorJobs }}</mat-card-title>
            <mat-card-subtitle>Errors</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
        <mat-card>
          <mat-card-header>
            <mat-icon mat-card-avatar>computer</mat-icon>
            <mat-card-title>{{ stats.activeWorkers }} / {{ stats.totalWorkers }}</mat-card-title>
            <mat-card-subtitle>Workers Online</mat-card-subtitle>
          </mat-card-header>
        </mat-card>
      </div>
    }
  `,
  styles: [`
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 16px;
      margin-top: 16px;
    }
  `],
})
export class DashboardComponent implements OnInit, OnDestroy {
  private readonly statsService = inject(StatsService);
  private sub?: Subscription;

  stats: DashboardStats | null = null;
  loading = true;

  ngOnInit(): void {
    this.sub = this.statsService.pollStats().subscribe({
      next: (stats) => {
        this.stats = stats;
        this.loading = false;
      },
      error: () => {
        this.loading = false;
      },
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }
}
