// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../../environments/environment';
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
  private readonly base = `${environment.apiBaseUrl}/setup`;

  getStatus(): Observable<SetupStatus> {
    return this.http.get<SetupStatus>(`${this.base}/status/`);
  }

  setTopology(req: TopologyRequest): Observable<TopologyResponse> {
    return this.http.post<TopologyResponse>(`${this.base}/topology/`, req);
  }

  configureNetwork(req: NetworkRequest): Observable<NetworkResponse> {
    return this.http.post<NetworkResponse>(`${this.base}/network/`, req);
  }

  configureDatabase(req: DatabaseRequest): Observable<DatabaseResponse> {
    return this.http.post<DatabaseResponse>(`${this.base}/database/`, req);
  }

  createAdminUser(req: AdminUserRequest): Observable<AdminUserResponse> {
    return this.http.post<AdminUserResponse>(`${this.base}/admin-user/`, req);
  }

  setWorkerPassword(req: WorkerPasswordRequest): Observable<WorkerPasswordResponse> {
    return this.http.post<WorkerPasswordResponse>(`${this.base}/worker-password/`, req);
  }

  startFfmpegDownload(): Observable<DownloadStartResponse> {
    return this.http.post<DownloadStartResponse>(`${this.base}/ffmpeg/start/`, {});
  }

  getFfmpegProgress(taskId: string): Observable<DownloadProgress> {
    return this.http.get<DownloadProgress>(`${this.base}/ffmpeg/progress/${taskId}/`);
  }

  cancelFfmpegDownload(): Observable<DownloadCancelResponse> {
    return this.http.post<DownloadCancelResponse>(`${this.base}/ffmpeg/cancel/`, {});
  }

  startBlenderDownload(): Observable<DownloadStartResponse> {
    return this.http.post<DownloadStartResponse>(`${this.base}/blender/start/`, {});
  }

  getBlenderProgress(taskId: string): Observable<DownloadProgress> {
    return this.http.get<DownloadProgress>(`${this.base}/blender/progress/${taskId}/`);
  }

  cancelBlenderDownload(): Observable<DownloadCancelResponse> {
    return this.http.post<DownloadCancelResponse>(`${this.base}/blender/cancel/`, {});
  }

  verify(): Observable<VerifyResponse> {
    return this.http.post<VerifyResponse>(`${this.base}/verify/`, {});
  }

  getSummary(): Observable<SetupSummary> {
    return this.http.get<SetupSummary>(`${this.base}/summary/`);
  }

  getHealth(): Observable<{ boot_id: string; setup_mode: boolean }> {
    return this.http.get<{ boot_id: string; setup_mode: boolean }>(
      `${environment.apiBaseUrl}/health/`,
    );
  }

  requestRestart(): Observable<void> {
    return this.http.post<void>(`${this.base}/restart/`, {});
  }
}
