// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../../environments/environment';
import { SetupStateService } from './setup-state.service';
import {
  SetupStatus,
  TopologyRequest,
  TopologyResponse,
  NetworkRequest,
  NetworkResponse,
  DatabaseRequest,
  DatabaseResponse,
  AdminUserRequest,
  AdminUserResponse,
  WorkerPasswordRequest,
  WorkerPasswordResponse,
  DownloadStartResponse,
  DownloadProgress,
  DownloadCancelResponse,
  VerifyResponse,
  SetupSummary,
} from '../models/setup.models';

@Injectable({ providedIn: 'root' })
export class SetupApiService {
  private readonly http = inject(HttpClient);
  private readonly stateService = inject(SetupStateService);
  private readonly base = `${environment.apiBaseUrl}/setup`;

  private postWithToken<T>(url: string, body: unknown = {}): Observable<T> {
    const headers: Record<string, string> = {};
    const token = this.stateService.setupToken;
    if (token) {
      headers['X-Setup-Token'] = token;
    }
    return this.http.post<T>(url, body, { headers });
  }

  getStatus(): Observable<SetupStatus> {
    return this.http.get<SetupStatus>(`${this.base}/status/`);
  }

  setTopology(req: TopologyRequest): Observable<TopologyResponse> {
    return this.postWithToken<TopologyResponse>(`${this.base}/topology/`, req);
  }

  configureNetwork(req: NetworkRequest): Observable<NetworkResponse> {
    return this.postWithToken<NetworkResponse>(`${this.base}/network/`, req);
  }

  configureDatabase(req: DatabaseRequest): Observable<DatabaseResponse> {
    return this.postWithToken<DatabaseResponse>(`${this.base}/database/`, req);
  }

  createAdminUser(req: AdminUserRequest): Observable<AdminUserResponse> {
    return this.postWithToken<AdminUserResponse>(`${this.base}/admin-user/`, req);
  }

  setWorkerPassword(req: WorkerPasswordRequest): Observable<WorkerPasswordResponse> {
    return this.postWithToken<WorkerPasswordResponse>(`${this.base}/worker-password/`, req);
  }

  startFfmpegDownload(): Observable<DownloadStartResponse> {
    return this.postWithToken<DownloadStartResponse>(`${this.base}/ffmpeg/start/`);
  }

  getFfmpegProgress(taskId: string): Observable<DownloadProgress> {
    return this.http.get<DownloadProgress>(`${this.base}/ffmpeg/progress/${taskId}/`);
  }

  cancelFfmpegDownload(): Observable<DownloadCancelResponse> {
    return this.postWithToken<DownloadCancelResponse>(`${this.base}/ffmpeg/cancel/`);
  }

  startBlenderDownload(): Observable<DownloadStartResponse> {
    return this.postWithToken<DownloadStartResponse>(`${this.base}/blender/start/`);
  }

  getBlenderProgress(taskId: string): Observable<DownloadProgress> {
    return this.http.get<DownloadProgress>(`${this.base}/blender/progress/${taskId}/`);
  }

  cancelBlenderDownload(): Observable<DownloadCancelResponse> {
    return this.postWithToken<DownloadCancelResponse>(`${this.base}/blender/cancel/`);
  }

  verify(): Observable<VerifyResponse> {
    return this.postWithToken<VerifyResponse>(`${this.base}/verify/`);
  }

  getSummary(): Observable<SetupSummary> {
    return this.http.get<SetupSummary>(`${this.base}/summary/`);
  }
}
