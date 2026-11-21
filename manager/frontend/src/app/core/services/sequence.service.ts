// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll, PollErrorHandler } from './polling.util';
import {
  CreateSubmissionRequest, DiscoveryKey, Page, SequenceDiscovery, SequenceSubmission,
} from './sequence.types';

/** The versions list polls every 10 s (FR-SU-3); a discovery every 3 s (FR-SU-2). */
export const VERSIONS_POLL_MS = 10_000;
export const DISCOVERY_POLL_MS = 3_000;

/** A download of a version or of one shot (FR-SL-1). */
export type SequenceDownload = 'video' | 'video_sound' | 'sound' | 'frames';

/** Unreal sequence discoveries and submissions (admin only on the manager). */
@Injectable({ providedIn: 'root' })
export class SequenceService {
  private readonly http = inject(HttpClient);
  private readonly discoveries = `${environment.apiBaseUrl}/sequence-discoveries`;
  private readonly submissions = `${environment.apiBaseUrl}/sequence-submissions`;

  /** The project's discoveries, newest first (first page only). */
  listDiscoveries(project: string): Observable<SequenceDiscovery[]> {
    const params = new HttpParams().set('project', project);
    return this.http.get<Page<SequenceDiscovery>>(`${this.discoveries}/`, { params }).pipe(map(p => p.results));
  }

  /** Start or reuse the reading of a sequence's shots. */
  discover(key: DiscoveryKey): Observable<SequenceDiscovery> {
    return this.http.post<SequenceDiscovery>(`${this.discoveries}/`, key);
  }

  getDiscovery(id: number): Observable<SequenceDiscovery> {
    return this.http.get<SequenceDiscovery>(`${this.discoveries}/${id}/`);
  }

  pollDiscovery(id: number, onError?: PollErrorHandler): Observable<SequenceDiscovery> {
    return poll(() => this.getDiscovery(id), onError, DISCOVERY_POLL_MS);
  }

  /** The project's versions, newest first (first page only). */
  list(project: string): Observable<SequenceSubmission[]> {
    const params = new HttpParams().set('project', project);
    return this.http.get<Page<SequenceSubmission>>(`${this.submissions}/`, { params }).pipe(map(p => p.results));
  }

  pollList(project: string, onError?: PollErrorHandler): Observable<SequenceSubmission[]> {
    return poll(() => this.list(project), onError, VERSIONS_POLL_MS);
  }

  get(id: number): Observable<SequenceSubmission> {
    return this.http.get<SequenceSubmission>(`${this.submissions}/${id}/`);
  }

  pollDetail(id: number, onError?: PollErrorHandler): Observable<SequenceSubmission> {
    return poll(() => this.get(id), onError, VERSIONS_POLL_MS);
  }

  create(body: CreateSubmissionRequest): Observable<SequenceSubmission> {
    return this.http.post<SequenceSubmission>(`${this.submissions}/`, body);
  }

  cancel(id: number): Observable<{ status: string; canceled: number }> {
    return this.http.post<{ status: string; canceled: number }>(`${this.submissions}/${id}/cancel/`, {});
  }

  rerenderShot(id: number, index: number): Observable<{ status: string; requeued: number }> {
    return this.http.post<{ status: string; requeued: number }>(
      `${this.submissions}/${id}/shots/${index}/rerender/`, {});
  }

  delete(id: number): Observable<void> {
    return this.http.delete<void>(`${this.submissions}/${id}/`);
  }

  retrySound(id: number): Observable<{ requeued: number }> {
    return this.http.post<{ requeued: number }>(`${this.submissions}/${id}/retry-sound/`, {});
  }

  retryVideo(id: number): Observable<{ reset: number }> {
    return this.http.post<{ reset: number }>(`${this.submissions}/${id}/retry-video/`, {});
  }

  mergeSound(id: number): Observable<{ status: string }> {
    return this.http.post<{ status: string }>(`${this.submissions}/${id}/merge-sound/`, {});
  }

  /** A plain link to a download (session cookie, never a blob: FR-SU-4, D17). */
  downloadUrl(id: number, kind: SequenceDownload, shotIndex?: number): string {
    const shot = shotIndex === undefined ? '' : `shots/${shotIndex}/`;
    const base = `${this.submissions}/${id}/${shot}`;
    switch (kind) {
      case 'video': return `${base}download-video/`;
      case 'video_sound': return `${base}download-video/?sound=1`;
      case 'sound': return `${base}download-sound/`;
      case 'frames': return `${base}download-frames/`;
    }
  }
}

/** The text of a refused action (`{"error"}`, `{"detail"}` or a field error object). */
export function sequenceErrorText(error: unknown, fallback: string): string {
  const body = (error as { error?: unknown } | null)?.error;
  if (typeof body === 'string' && body && !body.trimStart().startsWith('<')) return body;
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>;
    for (const key of ['error', 'detail']) {
      if (typeof record[key] === 'string') return record[key] as string;
    }
    const parts = Object.entries(record).map(([key, value]) =>
      `${key}: ${Array.isArray(value) ? value.join(' ') : String(value)}`);
    if (parts.length > 0) return parts.join('; ');
  }
  return fallback;
}
