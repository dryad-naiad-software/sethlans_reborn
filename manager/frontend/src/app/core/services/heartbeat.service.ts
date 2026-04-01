import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';

export interface HeartbeatPayload {
  hostname: string;
  ip_address: string;
  port: number;
  cpu_name: string;
  gpu_name: string;
  os: string;
  ui_url?: string;
}

/**
 * Reference service for worker heartbeat endpoint.
 * This is primarily used by the worker agent, not the manager UI.
 * Included here for API completeness and potential monitoring features.
 */
@Injectable({ providedIn: 'root' })
export class HeartbeatService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/heartbeat`;

  sendHeartbeat(payload: HeartbeatPayload): Observable<unknown> {
    return this.http.post(`${this.baseUrl}/`, payload);
  }
}
