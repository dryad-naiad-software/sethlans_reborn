// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription, switchMap } from 'rxjs';
import { JobService, Job } from '../../core/services/job.service';

@Component({
  selector: 'app-job-detail',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatProgressSpinnerModule],
  template: `
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (job) {
      <h1>Job #{{ job.id }}</h1>

      <mat-card>
        <mat-card-content>
          <div class="detail-grid">
            <div><strong>Status:</strong> {{ job.status }}</div>
            <div><strong>Render Engine:</strong> {{ job.render_engine }}</div>
            <div><strong>Render Device:</strong> {{ job.render_device }}</div>
            <div><strong>Cycles Feature Set:</strong> {{ job.cycles_feature_set }}</div>
            <div><strong>Frame:</strong> {{ job.frame_number }}</div>
            <div><strong>Tile:</strong> ({{ job.tile_x }}, {{ job.tile_y }})</div>
            <div><strong>Worker:</strong> {{ job.worker ?? 'Unassigned' }}</div>
            <div><strong>Created:</strong> {{ job.created_at | date:'medium' }}</div>
            <div><strong>Updated:</strong> {{ job.updated_at | date:'medium' }}</div>
          </div>
        </mat-card-content>
      </mat-card>

      @if (job.output_file) {
        <mat-card class="output-card">
          <mat-card-header>
            <mat-card-title>Output</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <img [src]="job.output_file" alt="Render output" class="output-image" />
          </mat-card-content>
        </mat-card>
      }
    } @else {
      <p>Job not found.</p>
    }
  `,
  styles: [`
    .detail-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .output-card { margin-top: 16px; }
    .output-image { max-width: 100%; height: auto; }
  `],
})
export class JobDetailComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly jobService = inject(JobService);
  private sub?: Subscription;

  job: Job | null = null;
  loading = true;

  ngOnInit(): void {
    this.sub = this.route.paramMap.pipe(
      switchMap(params => this.jobService.pollDetail(Number(params.get('id')))),
    ).subscribe({
      next: (job) => { this.job = job; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
