// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef, MatDialogModule } from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { JobService, Job } from '../../core/services/job.service';
import { TiledJob } from '../../core/services/tiled-job.service';
import { Animation, AnimationService } from '../../core/services/animation.service';
import { parseRenderSettings } from './render-payload.util';
import { triggerBlobDownload } from '../../core/services/download.util';
import { JobPrefillData } from './job-create-form.types';
import { AnimationFilmstripComponent } from './animation-filmstrip.component';
import { FilmstripFrame, fromAnimationFrame, fromJob } from './filmstrip-frame';
import { formatTime } from './project-jobs-table.util';

export interface JobResultDialogData {
  type: 'single' | 'tiled' | 'animation';
  job?: Job;
  tiledJob?: TiledJob;
  animation?: Animation;
  selectedFrameIndex?: number;
}

@Component({
  selector: 'app-job-result-dialog',
  standalone: true,
  imports: [
    MatDialogModule, MatButtonModule, MatIconModule, MatSnackBarModule,
    MatProgressSpinnerModule, AnimationFilmstripComponent,
  ],
  template: `
    <div class="dialog-header">
      <h2 mat-dialog-title>{{ title }}</h2>
      <button mat-icon-button mat-dialog-close aria-label="Close"><mat-icon>close</mat-icon></button>
    </div>
    <mat-dialog-content>
      @switch (data.type) {
        @case ('single') { @if (data.job; as job) {
          <div class="image-container">
            <img [src]="job.output_file" [alt]="job.name" />
          </div>
          <div class="details">
            <h3>Render Details</h3>
            <div class="detail-grid">
              <span>Engine: {{ job.render_engine }}</span>
              <span>Device: {{ job.render_device }}</span>
              <span>Time: {{ formatTime(job.render_time_seconds) }}</span>
              <span>Resolution: {{ parsed.resolutionX }}x{{ parsed.resolutionY }}</span>
              <span>Samples: {{ parsed.samples }}</span>
              <span>Worker: {{ job.assigned_worker_hostname || '--' }}</span>
              <span>Frame: {{ job.start_frame }}</span>
            </div>
          </div>
        }}
        @case ('tiled') { @if (data.tiledJob; as tj) {
          <div class="image-container">
            <img [src]="tj.output_file" [alt]="tj.name" />
          </div>
          <div class="details">
            <h3>Render Details</h3>
            <div class="detail-grid">
              <span>Engine: {{ tj.render_engine }}</span>
              <span>Device: {{ tj.render_device }}</span>
              <span>Time: {{ formatTime(tj.total_render_time_seconds) }}</span>
              <span>Resolution: {{ tj.final_resolution_x }}x{{ tj.final_resolution_y }}</span>
              <span>Tiling: {{ tj.tile_count_x }}x{{ tj.tile_count_y }}
                ({{ tj.tile_count_x * tj.tile_count_y }} tiles)</span>
              <span>Samples: {{ tiledParsed.samples }}</span>
            </div>
          </div>
        }}
        @case ('animation') { @if (data.animation; as anim) {
          @if (loadingFrames) {
            <div class="loading-frames"><mat-spinner diameter="32" /></div>
          } @else {
            <div class="image-container">
              @if (selectedFilmstripFrame?.outputFile) {
                <img [src]="selectedFilmstripFrame!.outputFile"
                     [alt]="'Frame ' + selectedFilmstripFrame!.frameNumber" />
              } @else {
                <div class="no-image"><mat-icon>image</mat-icon><span>No image</span></div>
              }
            </div>
            <div class="frame-info">
              Frame {{ selectedFilmstripFrame?.frameNumber ?? '--' }} of {{ anim.total_frames }}
            </div>
            <app-animation-filmstrip [frames]="filmstripFrames"
              [selectedFrameId]="selectedFilmstripFrame?.id ?? null"
              (frameSelected)="onFrameSelect($event)" />
          }
          <div class="details">
            <h3>Render Details</h3>
            <div class="detail-grid">
              <span>Progress: {{ anim.completed_frames }}/{{ anim.total_frames }} frames</span>
              <span>Engine: {{ anim.render_engine }}</span>
              <span>Device: {{ anim.render_device }}</span>
              <span>Time: {{ formatTime(anim.total_render_time_seconds) }}</span>
              <span>Resolution: {{ animParsed.resolutionX }}x{{ animParsed.resolutionY }}</span>
              <span>Samples: {{ animParsed.samples }}</span>
            </div>
          </div>
        }}
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-stroked-button (click)="onRerender()">
        <mat-icon>replay</mat-icon> Re-render
      </button>
      @if (data.type === 'animation') {
        <button mat-raised-button color="primary" (click)="onDownloadAll()"
                [disabled]="downloading">
          <mat-icon>archive</mat-icon> Download All (ZIP)
        </button>
      } @else {
        <a mat-raised-button color="primary" [href]="downloadUrl" download>
          <mat-icon>download</mat-icon> Download Image
        </a>
      }
    </mat-dialog-actions>
  `,
  styles: [`
    .dialog-header { display: flex; justify-content: space-between; align-items: center; }
    .dialog-header h2 { margin: 0; }
    .image-container { text-align: center; margin-bottom: 12px; }
    .image-container img { max-width: 100%; max-height: 60vh; border-radius: 4px; }
    .no-image {
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      height: 200px; color: rgba(0,0,0,0.3);
    }
    .no-image mat-icon { font-size: 48px; width: 48px; height: 48px; }
    .loading-frames { display: flex; justify-content: center; padding: 24px; }
    .frame-info { text-align: center; font-size: 14px; color: rgba(0,0,0,0.7); margin-bottom: 8px; }
    .details { margin: 12px 0; }
    .details h3 { margin: 0 0 8px; font-size: 15px; }
    .detail-grid {
      display: flex; flex-wrap: wrap; gap: 8px 24px; font-size: 14px; color: rgba(0,0,0,0.7);
    }
    @media (max-width: 600px) {
      .image-container img { max-height: 40vh; }
    }
  `],
})
export class JobResultDialogComponent {
  readonly data: JobResultDialogData = inject(MAT_DIALOG_DATA);
  private readonly dialogRef = inject(MatDialogRef<JobResultDialogComponent>);
  private readonly animationService = inject(AnimationService);
  private readonly jobService = inject(JobService);
  private readonly snackBar = inject(MatSnackBar);

  selectedFilmstripFrame: FilmstripFrame | null = null;
  filmstripFrames: FilmstripFrame[] = [];
  loadingFrames = false;
  downloading = false;
  readonly formatTime = formatTime;

  readonly parsed = this.data.job
    ? parseRenderSettings(this.data.job.render_settings) : { samples: undefined, resolutionX: undefined, resolutionY: undefined };
  readonly tiledParsed = this.data.tiledJob
    ? parseRenderSettings(this.data.tiledJob.render_settings) : { samples: undefined, resolutionX: undefined, resolutionY: undefined };
  readonly animParsed = this.data.animation
    ? parseRenderSettings(this.data.animation.render_settings) : { samples: undefined, resolutionX: undefined, resolutionY: undefined };

  readonly title = this.data.job?.name ?? this.data.tiledJob?.name ?? this.data.animation?.name ?? '';
  readonly downloadUrl = this.data.job?.output_file ?? this.data.tiledJob?.output_file ?? '';

  constructor() {
    if (this.data.type === 'animation' && this.data.animation) {
      const anim = this.data.animation;
      if (anim.frames?.length) {
        this.filmstripFrames = anim.frames.map(fromAnimationFrame);
        const idx = this.data.selectedFrameIndex ?? 0;
        this.selectedFilmstripFrame = this.filmstripFrames[idx] ?? this.filmstripFrames[0];
      } else if (anim.tiling_config === 'NONE') {
        this.loadingFrames = true;
        this.jobService.list({ animation: anim.id, status: 'DONE' }).subscribe({
          next: (jobs) => {
            const sorted = jobs.sort((a, b) => a.start_frame - b.start_frame);
            this.filmstripFrames = sorted.map(fromJob);
            if (this.filmstripFrames.length) {
              const idx = this.data.selectedFrameIndex ?? 0;
              this.selectedFilmstripFrame = this.filmstripFrames[idx] ?? this.filmstripFrames[0];
            }
            this.loadingFrames = false;
          },
          error: () => {
            this.loadingFrames = false;
            this.snackBar.open('Failed to load animation frames', 'Dismiss', { duration: 5000 });
          },
        });
      }
    }
  }

  onFrameSelect(frame: FilmstripFrame): void { this.selectedFilmstripFrame = frame; }

  onRerender(): void {
    const prefill = this.buildPrefill();
    this.dialogRef.close({ action: 'rerender', prefill });
  }

  onDownloadAll(): void {
    if (!this.data.animation) return;
    this.downloading = true;
    this.animationService.download(this.data.animation.id).subscribe({
      next: (blob) => {
        triggerBlobDownload(blob, `${this.data.animation!.name}.zip`);
        this.downloading = false;
      },
      error: () => {
        this.downloading = false;
        this.snackBar.open('Failed to download animation', 'Dismiss', { duration: 5000 });
      },
    });
  }

  private buildPrefill(): JobPrefillData {
    const type = this.data.type;
    if (type === 'single' && this.data.job) {
      const j = this.data.job;
      const s = parseRenderSettings(j.render_settings);
      return { renderType: 'single', renderEngine: j.render_engine, renderDevice: j.render_device,
        samples: s.samples, resolutionX: s.resolutionX, resolutionY: s.resolutionY, frame: j.start_frame };
    }
    if (type === 'tiled' && this.data.tiledJob) {
      const t = this.data.tiledJob;
      const s = parseRenderSettings(t.render_settings);
      return { renderType: 'tiled', renderEngine: t.render_engine, renderDevice: t.render_device,
        samples: s.samples, resolutionX: t.final_resolution_x, resolutionY: t.final_resolution_y,
        tilingConfig: `${t.tile_count_x}x${t.tile_count_y}` };
    }
    if (type === 'animation' && this.data.animation) {
      const a = this.data.animation;
      const s = parseRenderSettings(a.render_settings);
      return { renderType: 'animation', renderEngine: a.render_engine, renderDevice: a.render_device,
        samples: s.samples, resolutionX: s.resolutionX, resolutionY: s.resolutionY,
        startFrame: a.start_frame, endFrame: a.end_frame, frameStep: a.frame_step,
        animTilingConfig: a.tiling_config };
    }
    return { renderType: 'single' };
  }
}
