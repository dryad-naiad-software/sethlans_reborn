// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output, OnChanges, SimpleChanges, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDividerModule } from '@angular/material/divider';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { Animation, AnimationFrame, AnimationService } from '../../core/services/animation.service';
import { triggerBlobDownload } from '../../core/services/download.util';
import { formatTime } from './project-jobs-table.util';

const GRID_PAGE_SIZE = 16;

export interface FrameClickEvent {
  animation: Animation;
  frameIndex: number;
}

@Component({
  selector: 'app-animation-frames-section',
  standalone: true,
  imports: [MatButtonModule, MatIconModule, MatDividerModule, MatSnackBarModule],
  template: `
    <div class="section">
      <div class="section-header">
        <h3>Animation: {{ animation.name }}</h3>
        <div class="header-meta">
          <span>{{ animation.completed_frames }}/{{ animation.total_frames }} frames</span>
          <span>Status: {{ animation.status }}</span>
          <span>Time: {{ formatTime(animation.total_render_time_seconds) }}</span>
        </div>
      </div>
      <div class="frame-grid">
        @for (frame of visibleFrames; track frame.id) {
          <div class="grid-frame" (click)="onFrameClick(frame)">
            @if (frame.thumbnail) {
              <img [src]="frame.thumbnail" [alt]="'Frame ' + frame.frame_number"
                   width="80" height="80" />
            } @else {
              <div class="placeholder"><mat-icon>image</mat-icon></div>
            }
            <span class="frame-num">{{ frame.frame_number }}</span>
          </div>
        }
      </div>
      <div class="grid-footer">
        <span class="page-info">Page {{ currentPage }} of {{ totalPages }}</span>
        <div class="pagination">
          <button mat-button [disabled]="currentPage <= 1" (click)="prevPage()">
            <mat-icon>chevron_left</mat-icon> Prev
          </button>
          <button mat-button [disabled]="currentPage >= totalPages" (click)="nextPage()">
            Next <mat-icon>chevron_right</mat-icon>
          </button>
        </div>
        <button mat-raised-button color="primary" (click)="onDownload()" [disabled]="downloading">
          <mat-icon>archive</mat-icon> Download All (ZIP)
        </button>
      </div>
      <mat-divider />
    </div>
  `,
  styles: [`
    .section { margin-bottom: 16px; }
    .section-header { margin-bottom: 8px; }
    .section-header h3 { margin: 0 0 4px; }
    .header-meta { display: flex; gap: 16px; font-size: 14px; color: rgba(0,0,0,0.6); }
    .frame-grid {
      display: grid; grid-template-columns: repeat(auto-fill, minmax(88px, 1fr));
      gap: 8px; margin-bottom: 8px;
    }
    .grid-frame { cursor: pointer; text-align: center; }
    .grid-frame img {
      width: 80px; height: 80px; object-fit: cover; border-radius: 4px; display: block;
      margin: 0 auto;
    }
    .placeholder {
      width: 80px; height: 80px; display: flex; align-items: center;
      justify-content: center; background: #f5f5f5; border-radius: 4px; margin: 0 auto;
    }
    .placeholder mat-icon { color: rgba(0,0,0,0.3); }
    .frame-num { font-size: 12px; color: rgba(0,0,0,0.6); }
    .grid-footer {
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 8px; margin-bottom: 12px;
    }
    .pagination { display: flex; gap: 4px; }
    .page-info { font-size: 13px; color: rgba(0,0,0,0.6); }
  `],
})
export class AnimationFramesSectionComponent implements OnChanges {
  @Input() animation!: Animation;
  @Output() frameClick = new EventEmitter<FrameClickEvent>();

  private readonly animationService = inject(AnimationService);
  private readonly snackBar = inject(MatSnackBar);
  readonly formatTime = formatTime;

  currentPage = 1;
  totalPages = 1;
  visibleFrames: AnimationFrame[] = [];
  downloading = false;

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['animation']) {
      this.totalPages = Math.max(1, Math.ceil((this.animation.frames?.length ?? 0) / GRID_PAGE_SIZE));
      if (this.currentPage > this.totalPages) this.currentPage = this.totalPages;
      this.updateVisible();
    }
  }

  prevPage(): void {
    if (this.currentPage > 1) { this.currentPage--; this.updateVisible(); }
  }

  nextPage(): void {
    if (this.currentPage < this.totalPages) { this.currentPage++; this.updateVisible(); }
  }

  onFrameClick(frame: AnimationFrame): void {
    const idx = this.animation.frames.findIndex(f => f.id === frame.id);
    this.frameClick.emit({ animation: this.animation, frameIndex: idx >= 0 ? idx : 0 });
  }

  onDownload(): void {
    this.downloading = true;
    this.animationService.download(this.animation.id).subscribe({
      next: (blob) => {
        triggerBlobDownload(blob, `${this.animation.name}.zip`);
        this.downloading = false;
      },
      error: () => {
        this.downloading = false;
        this.snackBar.open('Failed to download animation', 'Dismiss', { duration: 5000 });
      },
    });
  }

  private updateVisible(): void {
    const frames = this.animation.frames ?? [];
    const start = (this.currentPage - 1) * GRID_PAGE_SIZE;
    this.visibleFrames = frames.slice(start, start + GRID_PAGE_SIZE);
  }
}
