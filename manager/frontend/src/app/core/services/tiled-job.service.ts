import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface TiledJob {
  id: number;
  project: number;
  name: string;
  tiling_configuration: string;
  status: string;
  progress: number;
  output_file: string;
  created_at: string;
  updated_at: string;
}

export interface CreateTiledJobRequest {
  project: number;
  frame_number: number;
  tiling_configuration: string;
  render_engine?: string;
  render_device?: string;
}

@Injectable({ providedIn: 'root' })
export class TiledJobService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/tiled-jobs`;

  list(): Observable<TiledJob[]> {
    return this.http.get<TiledJob[]>(`${this.baseUrl}/`);
  }

  pollList(): Observable<TiledJob[]> {
    return poll(() => this.list());
  }

  get(id: number): Observable<TiledJob> {
    return this.http.get<TiledJob>(`${this.baseUrl}/${id}/`);
  }

  pollDetail(id: number): Observable<TiledJob> {
    return poll(() => this.get(id));
  }

  create(data: CreateTiledJobRequest): Observable<TiledJob> {
    return this.http.post<TiledJob>(`${this.baseUrl}/`, data);
  }
}
