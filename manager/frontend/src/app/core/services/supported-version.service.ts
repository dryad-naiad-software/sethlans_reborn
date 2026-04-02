// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface SupportedVersion {
  id: number;
  major: number;
  minor: number;
  series: string;
  resolved_version: string;
  is_default: boolean;
  added_at: string;
  last_patch_check: string | null;
}

export interface DeletePreview {
  affected_project_count: number;
  affected_job_count: number;
  migration_target: { series: string; resolved_version: string } | null;
  warning: string;
}

export interface DeleteResult {
  migrated_project_count: number;
  affected_job_count: number;
  new_default_version: string | null;
  warning: string;
}

export interface AvailableSeriesResponse {
  series: string[];
  cache_ready: boolean;
}

@Injectable({ providedIn: 'root' })
export class SupportedVersionService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/supported-versions`;

  list(): Observable<SupportedVersion[]> {
    return this.http.get<SupportedVersion[]>(`${this.baseUrl}/`);
  }

  create(series: string, isDefault = false): Observable<SupportedVersion> {
    return this.http.post<SupportedVersion>(`${this.baseUrl}/`, {
      series,
      is_default: isDefault,
    });
  }

  setDefault(id: number): Observable<SupportedVersion> {
    return this.http.patch<SupportedVersion>(`${this.baseUrl}/${id}/`, {
      is_default: true,
    });
  }

  previewDelete(id: number): Observable<DeletePreview> {
    return this.http.delete<DeletePreview>(`${this.baseUrl}/${id}/`);
  }

  confirmDelete(id: number): Observable<DeleteResult> {
    const params = new HttpParams().set('confirm', 'true');
    return this.http.delete<DeleteResult>(`${this.baseUrl}/${id}/`, { params });
  }

  availableSeries(): Observable<AvailableSeriesResponse> {
    return this.http.get<AvailableSeriesResponse>(
      `${this.baseUrl}/available_series/`,
    );
  }
}
