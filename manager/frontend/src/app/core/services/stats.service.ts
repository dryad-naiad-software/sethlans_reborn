import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, map } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface DashboardStats {
  totalProjects: number;
  activeJobs: number;
  completedJobs: number;
  errorJobs: number;
  queuedJobs: number;
  totalWorkers: number;
  activeWorkers: number;
}

interface StatsResponse {
  workers: { total: number; active: number };
  jobs: { queued: number; rendering: number; done: number; error: number };
  projects: { total: number };
  recent_completions: unknown[];
}

@Injectable({ providedIn: 'root' })
export class StatsService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBaseUrl;

  getStats(): Observable<DashboardStats> {
    return this.http.get<StatsResponse>(`${this.apiBase}/stats/`).pipe(
      map(data => ({
        totalProjects: data.projects.total,
        queuedJobs: data.jobs.queued,
        activeJobs: data.jobs.rendering,
        completedJobs: data.jobs.done,
        errorJobs: data.jobs.error,
        totalWorkers: data.workers.total,
        activeWorkers: data.workers.active,
      })),
    );
  }

  pollStats(): Observable<DashboardStats> {
    return poll(() => this.getStats());
  }
}
