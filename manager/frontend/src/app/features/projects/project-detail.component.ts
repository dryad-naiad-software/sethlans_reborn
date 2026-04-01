import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTabsModule } from '@angular/material/tabs';
import { Subscription, switchMap } from 'rxjs';
import { ProjectService, Project } from '../../core/services/project.service';

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [
    CommonModule, MatCardModule,
    MatButtonModule, MatIconModule, MatProgressSpinnerModule, MatTabsModule,
  ],
  template: `
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (project) {
      <div class="header">
        <h1>{{ project.name }}</h1>
        <div>
          <button mat-raised-button (click)="togglePause()">
            <mat-icon>{{ project.status === 'PAUSED' ? 'play_arrow' : 'pause' }}</mat-icon>
            {{ project.status === 'PAUSED' ? 'Unpause' : 'Pause' }}
          </button>
        </div>
      </div>

      <mat-card>
        <mat-card-content>
          <p><strong>Status:</strong> {{ project.status }}</p>
          <p><strong>Description:</strong> {{ project.description }}</p>
          <p><strong>Created:</strong> {{ project.created_at | date:'medium' }}</p>
        </mat-card-content>
      </mat-card>

      <mat-tab-group class="tabs">
        <mat-tab label="Assets">
          <p>Assets for this project will be listed here.</p>
        </mat-tab>
        <mat-tab label="Jobs">
          <p>Jobs for this project will be listed here.</p>
        </mat-tab>
        <mat-tab label="Animations">
          <p>Animations for this project will be listed here.</p>
        </mat-tab>
        <mat-tab label="Tiled Jobs">
          <p>Tiled jobs for this project will be listed here.</p>
        </mat-tab>
      </mat-tab-group>
    } @else {
      <p>Project not found.</p>
    }
  `,
  styles: [`
    .header { display: flex; justify-content: space-between; align-items: center; }
    .tabs { margin-top: 16px; }
    mat-card { margin-top: 16px; }
  `],
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly projectService = inject(ProjectService);
  private sub?: Subscription;

  project: Project | null = null;
  loading = true;

  ngOnInit(): void {
    this.sub = this.route.paramMap.pipe(
      switchMap(params => this.projectService.get(Number(params.get('id')))),
    ).subscribe({
      next: (project) => { this.project = project; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  togglePause(): void {
    if (!this.project) return;
    const action = this.project.status === 'PAUSED'
      ? this.projectService.unpause(this.project.id)
      : this.projectService.pause(this.project.id);
    action.subscribe(p => this.project = p);
  }
}
