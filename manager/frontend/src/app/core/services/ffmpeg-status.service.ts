// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, Signal, computed, inject, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, finalize, of, shareReplay, tap } from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Detailed FFmpeg status block returned only to staff/admin users.
 * Mirrors the manager-side FFmpegStatusAdminSerializer output.
 */
export interface FFmpegDetails {
  source: 'system' | 'bundled';
  version: string;
  path: string;
  status: 'ready' | 'installing' | 'failed';
  error: string | null;
}

/**
 * Discriminated-union DTO for `GET /api/ffmpeg-status/`.
 *
 * The `ffmpeg` block is present on the admin payload (when `request.user.is_staff`)
 * and absent on the regular payload. Components branch via `if (status.ffmpeg)`.
 */
export interface FFmpegStatusResponse {
  video_assembly_ready: boolean;
  ffmpeg?: FFmpegDetails;
}

/**
 * FFmpegStatusService — exposes manager-side video-assembly readiness.
 *
 * Issues `GET /api/ffmpeg-status/` lazily on first call to `load()` and caches
 * the result for the lifetime of the page (status changes at most once per
 * manager process, on the boot transition installing → ready / failed).
 *
 * Default `videoAssemblyReady` is `false` until the response arrives — fail-closed.
 */
@Injectable({ providedIn: 'root' })
export class FFmpegStatusService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/ffmpeg-status`;

  private readonly readyState = signal(false);
  private readonly detailsState = signal<FFmpegDetails | undefined>(undefined);
  private readonly loadingState = signal(false);

  private inflight$: Observable<FFmpegStatusResponse> | null = null;
  private fetched = false;

  readonly videoAssemblyReady: Signal<boolean> = computed(() => this.readyState());
  readonly details: Signal<FFmpegDetails | undefined> = computed(() => this.detailsState());
  readonly loading: Signal<boolean> = computed(() => this.loadingState());

  /**
   * Trigger the one-time fetch (idempotent across the page lifetime).
   * Returns an Observable that emits the cached or freshly-fetched status.
   */
  load(): Observable<FFmpegStatusResponse> {
    if (this.fetched) {
      const ffmpeg = this.detailsState();
      const cached: FFmpegStatusResponse = { video_assembly_ready: this.readyState() };
      if (ffmpeg) {
        cached.ffmpeg = ffmpeg;
      }
      return of(cached);
    }
    if (this.inflight$) {
      return this.inflight$;
    }
    this.loadingState.set(true);
    this.inflight$ = this.http.get<FFmpegStatusResponse>(`${this.baseUrl}/`).pipe(
      tap(resp => {
        this.readyState.set(!!resp.video_assembly_ready);
        this.detailsState.set(resp.ffmpeg);
        this.fetched = true;
      }),
      finalize(() => {
        this.loadingState.set(false);
        this.inflight$ = null;
      }),
      shareReplay({ bufferSize: 1, refCount: false }),
    );
    return this.inflight$;
  }
}
