import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, of } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface Project {
  id: number;
  name: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
}

@Injectable({ providedIn: 'root' })
export class ProjectService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = `${environment.apiBaseUrl}/projects`;

  list(): Observable<Project[]> {
    return this.http.get<Project[]>(`${this.baseUrl}/`);
  }

  pollList(): Observable<Project[]> {
    return poll(() => this.list());
  }

  get(id: number): Observable<Project> {
    return this.http.get<Project>(`${this.baseUrl}/${id}/`);
  }

  create(data: Partial<Project>): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/`, data);
  }

  update(id: number, data: Partial<Project>): Observable<Project> {
    return this.http.patch<Project>(`${this.baseUrl}/${id}/`, data);
  }

  delete(id: number): Observable<void> {
    return this.http.delete<void>(`${this.baseUrl}/${id}/`);
  }

  pause(id: number): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/${id}/pause/`, {});
  }

  unpause(id: number): Observable<Project> {
    return this.http.post<Project>(`${this.baseUrl}/${id}/unpause/`, {});
  }
}
