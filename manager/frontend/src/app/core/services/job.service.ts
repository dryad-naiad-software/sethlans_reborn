// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';
import { Asset } from './asset.service';

export interface EffectiveBlenderVersion {
  series: string;
  resolved_version: string;
}

export interface Job {
  id: number;
  name: string;
  asset: Asset;
  output_file_pattern: string;
  start_frame: number;
  end_frame: number;
  status: string;
  status_display: string;
  assigned_worker: number | null;
  assigned_worker_hostname: string | null;
  animation: number | null;
  tiled_job: string | null;
  animation_frame: number | null;
  submitted_at: string;
  started_at: string | null;
  completed_at: string | null;
  blender_version: number | null;
  effective_blender_version: EffectiveBlenderVersion;
  render_engine: string;
  render_device: string;
  cycles_feature_set: string;
  render_settings: Record<string, unknown>;
  last_output: string;
  error_message: string;
  render_time_seconds: number | null;
  output_file: string | null;
  thumbnail: string | null;
}

export interface CreateJobRequest {
  name: string;
  asset_id: number;
  output_file_pattern: string;
  start_frame: number;
  end_frame: number;
  render_engine: string;
  render_device: string;
  render_settings?: Record<string, unknown>;
}

export interface JobFilter {
  status?: string;
  asset__project?: string;
}

@Injectable({ providedIn: 'root' })
export class JobService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/jobs`;

  list(filters?: JobFilter): Observable<Job[]> {
    let params = new HttpParams();
    if (filters?.status) params = params.set('status', filters.status);
    if (filters?.asset__project) params = params.set('asset__project', filters.asset__project);
    return this.http.get<Job[]>(`${this.baseUrl}/`, { params });
  }

  pollList(filters?: JobFilter): Observable<Job[]> {
    return poll(() => this.list(filters));
  }

  get(id: number): Observable<Job> {
    return this.http.get<Job>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: number): Observable<Job> {
    return poll(() => this.get(id));
  }

  create(data: CreateJobRequest): Observable<Job> {
    return this.http.post<Job>(`${this.baseUrl}/`, data);
  }
}
