// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output, OnChanges, SimpleChanges } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { AnimationFrame } from '../../core/services/animation.service';

const FRAMES_PER_PAGE = 5;

@Component({
  selector: 'app-animation-filmstrip',
  standalone: true,
  imports: [MatButtonModule, MatIconModule],
  template: `
    <div class="filmstrip">
      <button mat-icon-button (click)="prevPage()" [disabled]="currentPage <= 1"
              aria-label="Previous page">
        <mat-icon>chevron_left</mat-icon>
      </button>
      <div class="frames">
        @for (frame of visibleFrames; track frame.id) {
          <div class="frame-slot" [class.selected]="frame.id === selectedFrameId"
               (click)="onFrameClick(frame)">
            @if (frame.thumbnail) {
              <img [src]="frame.thumbnail" [alt]="'Frame ' + frame.frame_number"
                   width="80" height="80" />
            } @else {
              <div class="placeholder">
                <mat-icon>image</mat-icon>
              </div>
            }
            <span class="frame-label">{{ frame.frame_number }}</span>
          </div>
        }
      </div>
      <button mat-icon-button (click)="nextPage()" [disabled]="currentPage >= totalPages"
              aria-label="Next page">
        <mat-icon>chevron_right</mat-icon>
      </button>
    </div>
    <div class="page-info">Page {{ currentPage }} of {{ totalPages }}</div>
  `,
  styles: [`
    .filmstrip { display: flex; align-items: center; gap: 4px; }
    .frames { display: flex; gap: 8px; }
    .frame-slot {
      cursor: pointer; text-align: center; border: 2px solid transparent;
      border-radius: 6px; padding: 2px; transition: border-color 0.2s;
    }
    .frame-slot.selected { border-color: #1976d2; }
    .frame-slot img {
      width: 80px; height: 80px; object-fit: cover; border-radius: 4px; display: block;
    }
    .placeholder {
      width: 80px; height: 80px; display: flex; align-items: center;
      justify-content: center; background: #f5f5f5; border-radius: 4px;
    }
    .placeholder mat-icon { color: rgba(0,0,0,0.3); }
    .frame-label { font-size: 12px; color: rgba(0,0,0,0.6); margin-top: 2px; display: block; }
    .page-info { text-align: center; font-size: 13px; color: rgba(0,0,0,0.6); margin-top: 4px; }
  `],
})
export class AnimationFilmstripComponent implements OnChanges {
  @Input() frames: AnimationFrame[] = [];
  @Input() selectedFrameId: number | null = null;
  @Output() frameSelected = new EventEmitter<AnimationFrame>();

  currentPage = 1;
  totalPages = 1;
  visibleFrames: AnimationFrame[] = [];

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['frames']) {
      this.totalPages = Math.max(1, Math.ceil(this.frames.length / FRAMES_PER_PAGE));
      if (this.currentPage > this.totalPages) this.currentPage = this.totalPages;
      this.updateVisibleFrames();
    }
    if (changes['selectedFrameId'] && this.selectedFrameId != null) {
      this.scrollToFrame(this.selectedFrameId);
    }
  }

  prevPage(): void {
    if (this.currentPage > 1) { this.currentPage--; this.updateVisibleFrames(); }
  }

  nextPage(): void {
    if (this.currentPage < this.totalPages) { this.currentPage++; this.updateVisibleFrames(); }
  }

  onFrameClick(frame: AnimationFrame): void {
    this.frameSelected.emit(frame);
  }

  private updateVisibleFrames(): void {
    const start = (this.currentPage - 1) * FRAMES_PER_PAGE;
    this.visibleFrames = this.frames.slice(start, start + FRAMES_PER_PAGE);
  }

  private scrollToFrame(frameId: number): void {
    const idx = this.frames.findIndex(f => f.id === frameId);
    if (idx >= 0) {
      const page = Math.floor(idx / FRAMES_PER_PAGE) + 1;
      if (page !== this.currentPage) {
        this.currentPage = page;
        this.updateVisibleFrames();
      }
    }
  }
}
