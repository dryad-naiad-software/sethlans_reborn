// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, EventEmitter, Input, Output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Animation } from '../../core/services/animation.service';
import { CONTAINER_CONTENT_TYPES } from './render-payload.util';

@Component({
  selector: 'app-animation-video-player',
  standalone: true,
  imports: [
    MatButtonModule, MatIconModule, MatProgressSpinnerModule, MatTooltipModule,
  ],
  template: `
    @switch (anim.video_status) {
      @case ('DONE') {
        <div class="video-container">
          <video controls preload="metadata" class="video-player">
            <source [src]="anim.video_file" [type]="videoMimeType" />
            Your browser does not support the video tag.
          </video>
        </div>
      }
      @case ('ASSEMBLING') {
        <div class="assembling-status">
          <mat-spinner diameter="32" />
          <span>Assembling video...</span>
        </div>
      }
      @case ('ERROR') {
        <div class="error-card">
          <div class="error-header">
            <mat-icon color="warn">error</mat-icon>
            <span>Video assembly failed</span>
          </div>
          @if (anim.video_error) {
            <p class="error-message" [matTooltip]="anim.video_error">
              {{ truncatedError }}
            </p>
          }
          <button mat-stroked-button color="warn" (click)="retryRequested.emit()">
            <mat-icon>replay</mat-icon> Retry Assembly
          </button>
        </div>
      }
    }
  `,
  styles: [`
    .video-container { text-align: center; margin-bottom: 12px; }
    .video-player {
      max-width: 100%; max-height: 60vh; border-radius: 4px;
    }
    .assembling-status {
      display: flex; flex-direction: column; align-items: center;
      gap: 12px; padding: 24px; color: rgba(0,0,0,0.7);
    }
    .error-card {
      background: #fff3f3; border: 1px solid #ffcdd2; border-radius: 8px;
      padding: 16px; margin-bottom: 12px;
    }
    .error-header {
      display: flex; align-items: center; gap: 8px;
      font-weight: 500; margin-bottom: 8px;
    }
    .error-message {
      font-size: 13px; color: rgba(0,0,0,0.6);
      margin: 0 0 12px; word-break: break-word;
    }
  `],
})
export class AnimationVideoPlayerComponent {
  @Input() anim!: Animation;
  @Output() retryRequested = new EventEmitter<void>();
  @Output() downloadRequested = new EventEmitter<void>();

  get videoMimeType(): string {
    const container = this.anim.video_settings?.container ?? 'mp4';
    return CONTAINER_CONTENT_TYPES[container] ?? 'video/mp4';
  }

  get truncatedError(): string {
    const err = this.anim.video_error ?? '';
    return err.length > 200 ? err.substring(0, 200) + '...' : err;
  }
}
