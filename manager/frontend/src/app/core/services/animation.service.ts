// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';
import { Project } from './project.service';
import { Asset } from './asset.service';
import { EffectiveBlenderVersion } from './job.service';

export interface AnimationFrame {
  id: number;
  frame_number: number;
  status: string;
  output_file: string | null;
  thumbnail: string | null;
  render_time_seconds: number | null;
}

export interface Animation {
  id: number;
  name: string;
  status: string;
  progress: string;
  total_frames: number;
  completed_frames: number;
  project: string;
  project_details: Project;
  asset: Asset;
  output_file_pattern: string;
  start_frame: number;
  end_frame: number;
  frame_step: number;
  blender_version: number | null;
  effective_blender_version: EffectiveBlenderVersion;
  render_engine: string;
  render_device: string;
  cycles_feature_set: string;
  render_settings: Record<string, unknown>;
  tiling_config: string;
  submitted_at: string;
  completed_at: string | null;
  total_render_time_seconds: number;
  thumbnail: string | null;
  frames: AnimationFrame[];
}

export interface CreateAnimationRequest {
  name: string;
  project: string;
  asset_id: number;
  output_file_pattern: string;
  start_frame: number;
  end_frame: number;
  frame_step: number;
  tiling_config: string;
  render_engine: string;
  render_device: string;
  render_settings?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class AnimationService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/animations`;

  list(filters?: { project?: string }): Observable<Animation[]> {
    let params = new HttpParams();
    if (filters?.project) params = params.set('project', filters.project);
    return this.http.get<Animation[]>(`${this.baseUrl}/`, { params });
  }

  pollList(filters?: { project?: string }): Observable<Animation[]> {
    return poll(() => this.list(filters));
  }

  get(id: number): Observable<Animation> {
    return this.http.get<Animation>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: number): Observable<Animation> {
    return poll(() => this.get(id));
  }

  create(data: CreateAnimationRequest): Observable<Animation> {
    return this.http.post<Animation>(`${this.baseUrl}/`, data);
  }

  download(id: number): Observable<Blob> {
    return this.http.get(`${this.baseUrl}/${id}/download/`, { responseType: 'blob' });
  }

  pause(id: number): Observable<{ paused: number }> {
    return this.http.post<{ paused: number }>(`${this.baseUrl}/${id}/pause/`, {});
  }

  unpause(id: number): Observable<{ unpaused: number }> {
    return this.http.post<{ unpaused: number }>(`${this.baseUrl}/${id}/unpause/`, {});
  }

  requeue(id: number): Observable<{ requeued: number }> {
    return this.http.post<{ requeued: number }>(`${this.baseUrl}/${id}/requeue/`, {});
  }

  delete(id: number): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/`);
  }
}
