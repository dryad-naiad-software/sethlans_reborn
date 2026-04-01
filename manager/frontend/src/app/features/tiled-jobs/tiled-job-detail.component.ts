import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription, switchMap } from 'rxjs';
import { TiledJobService, TiledJob } from '../../core/services/tiled-job.service';

@Component({
  selector: 'app-tiled-job-detail',
  standalone: true,
  imports: [
    CommonModule, MatCardModule, MatProgressBarModule, MatProgressSpinnerModule,
  ],
  template: `
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (tiledJob) {
      <h1>Tiled Job #{{ tiledJob.id }}</h1>

      <mat-card>
        <mat-card-content>
          <p><strong>Status:</strong> {{ tiledJob.status }}</p>
          <p><strong>Tiling:</strong> {{ tiledJob.tiling_configuration }}</p>
          <p><strong>Progress:</strong></p>
          <mat-progress-bar mode="determinate" [value]="tiledJob.progress" />
          <p>{{ tiledJob.progress }}% complete</p>
          <p><strong>Created:</strong> {{ tiledJob.created_at | date:'medium' }}</p>
        </mat-card-content>
      </mat-card>

      <h2>Tile Grid</h2>
      <div class="tile-grid" [style.grid-template-columns]="gridColumns">
        @for (tile of tiles; track tile.index) {
          <div class="tile" [class]="tile.status">
            {{ tile.index + 1 }}
          </div>
        }
      </div>

      @if (tiledJob.output_file) {
        <mat-card class="output-card">
          <mat-card-header>
            <mat-card-title>Assembled Output</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <img [src]="tiledJob.output_file" alt="Assembled render" class="output-image" />
          </mat-card-content>
        </mat-card>
      }
    } @else {
      <p>Tiled job not found.</p>
    }
  `,
  styles: [`
    mat-card { margin-top: 16px; }
    h2 { margin-top: 24px; }
    .tile-grid {
      display: grid;
      gap: 4px;
      margin-top: 8px;
      max-width: 400px;
    }
    .tile {
      aspect-ratio: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      border: 1px solid #ccc;
      border-radius: 4px;
      font-size: 12px;
      background-color: #f5f5f5;
    }
    .tile.DONE { background-color: #c8e6c9; }
    .tile.RENDERING { background-color: #fff9c4; }
    .tile.ERROR { background-color: #ffcdd2; }
    .output-card { margin-top: 16px; }
    .output-image { max-width: 100%; height: auto; }
  `],
})
export class TiledJobDetailComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly tiledJobService = inject(TiledJobService);
  private sub?: Subscription;

  tiledJob: TiledJob | null = null;
  loading = true;
  tiles: { index: number; status: string }[] = [];
  gridColumns = '';

  ngOnInit(): void {
    this.sub = this.route.paramMap.pipe(
      switchMap(params =>
        this.tiledJobService.pollDetail(Number(params.get('id')))
      ),
    ).subscribe({
      next: (job) => {
        this.tiledJob = job;
        this.loading = false;
        this.buildGrid(job.tiling_configuration);
      },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  private buildGrid(config: string): void {
    // Parse tiling config like "2x2", "3x3", "4x4"
    const match = config.match(/(\d+)x(\d+)/);
    const cols = match ? parseInt(match[1], 10) : 2;
    const rows = match ? parseInt(match[2], 10) : 2;
    this.gridColumns = `repeat(${cols}, 1fr)`;
    const total = cols * rows;
    this.tiles = Array.from({ length: total }, (_, i) => ({
      index: i,
      status: 'QUEUED',
    }));
  }
}
