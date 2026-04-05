// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpEvent, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Project } from './project.service';

export interface Asset {
  id: number;
  name: string;
  blend_file: string;
  created_at: string;
  project: string;
  project_details: Project;
}

@Injectable({ providedIn: 'root' })
export class AssetService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/assets`;

  list(filters?: { project?: string }): Observable<Asset[]> {
    let params = new HttpParams();
    if (filters?.project) params = params.set('project', filters.project);
    return this.http.get<Asset[]>(`${this.baseUrl}/`, { params });
  }

  upload(projectId: string, name: string, file: File): Observable<HttpEvent<Asset>> {
    const formData = new FormData();
    formData.append('project', projectId);
    formData.append('name', name);
    formData.append('blend_file', file);
    return this.http.post<Asset>(`${this.baseUrl}/`, formData, {
      reportProgress: true,
      observe: 'events',
    });
  }
}
