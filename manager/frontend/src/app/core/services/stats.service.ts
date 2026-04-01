import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, forkJoin, map } from 'rxjs';
import { environment } from '../../../environments/environment';
import { poll } from './polling.util';

export interface DashboardStats {
  totalProjects: number;
  totalJobs: number;
  activeJobs: number;
  completedJobs: number;
  errorJobs: number;
  totalWorkers: number;
  activeWorkers: number;
}

@Injectable({ providedIn: 'root' })
export class StatsService {
  private readonly http = inject(HttpClient);
  private readonly apiBase = environment.apiBaseUrl;

  /**
   * Aggregates stats from multiple endpoints.
   * Will be refined once a dedicated stats endpoint exists.
   */
  getStats(): Observable<DashboardStats> {
    return forkJoin({
      projects: this.http.get<unknown[]>(`${this.apiBase}/projects/`),
      jobs: this.http.get<unknown[]>(`${this.apiBase}/jobs/`),
      workers: this.http.get<unknown[]>(`${this.apiBase}/heartbeat/`),
    }).pipe(
      map(({ projects, jobs, workers }) => {
        const jobStatuses = jobs.map((j: Record<string, unknown>) => j['status'] as string);
        return {
          totalProjects: projects.length,
          totalJobs: jobs.length,
          activeJobs: jobStatuses.filter(s => s === 'RENDERING').length,
          completedJobs: jobStatuses.filter(s => s === 'DONE').length,
          errorJobs: jobStatuses.filter(s => s === 'ERROR').length,
          totalWorkers: workers.length,
          activeWorkers: workers.filter(
            (w: Record<string, unknown>) => w['status'] === 'ACTIVE'
          ).length,
        };
      }),
    );
  }

  pollStats(): Observable<DashboardStats> {
    return poll(() => this.getStats());
  }
}
