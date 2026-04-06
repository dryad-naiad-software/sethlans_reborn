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

export interface TiledJob {
  id: string;
  name: string;
  status: string;
  progress: string;
  total_tiles: number;
  completed_tiles: number;
  project: string;
  project_details: Project;
  asset: Asset;
  final_resolution_x: number;
  final_resolution_y: number;
  tile_count_x: number;
  tile_count_y: number;
  blender_version: number | null;
  effective_blender_version: EffectiveBlenderVersion;
  render_engine: string;
  render_device: string;
  cycles_feature_set: string;
  render_settings: Record<string, unknown>;
  submitted_at: string;
  completed_at: string | null;
  total_render_time_seconds: number;
  output_file: string | null;
  thumbnail: string | null;
}

export interface CreateTiledJobRequest {
  name: string;
  project: string;
  asset_id: number;
  final_resolution_x: number;
  final_resolution_y: number;
  tile_count_x: number;
  tile_count_y: number;
  render_engine: string;
  render_device: string;
  render_settings?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class TiledJobService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/tiled-jobs`;

  list(filters?: { project?: string }): Observable<TiledJob[]> {
    let params = new HttpParams();
    if (filters?.project) params = params.set('project', filters.project);
    return this.http.get<TiledJob[]>(`${this.baseUrl}/`, { params });
  }

  pollList(filters?: { project?: string }): Observable<TiledJob[]> {
    return poll(() => this.list(filters));
  }

  get(id: string): Observable<TiledJob> {
    return this.http.get<TiledJob>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: string): Observable<TiledJob> {
    return poll(() => this.get(id));
  }

  create(data: CreateTiledJobRequest): Observable<TiledJob> {
    return this.http.post<TiledJob>(`${this.baseUrl}/`, data);
  }

  pause(id: string): Observable<{ paused: number }> {
    return this.http.post<{ paused: number }>(`${this.baseUrl}/${id}/pause/`, {});
  }

  unpause(id: string): Observable<{ unpaused: number }> {
    return this.http.post<{ unpaused: number }>(`${this.baseUrl}/${id}/unpause/`, {});
  }

  delete(id: string): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/`);
  }
}
