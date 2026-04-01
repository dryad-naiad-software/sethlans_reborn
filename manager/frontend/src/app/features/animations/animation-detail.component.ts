import { Component, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription, switchMap } from 'rxjs';
import { AnimationService, Animation } from '../../core/services/animation.service';

@Component({
  selector: 'app-animation-detail',
  standalone: true,
  imports: [
    CommonModule, MatCardModule, MatProgressBarModule, MatProgressSpinnerModule,
  ],
  template: `
    @if (loading) {
      <mat-spinner diameter="40" />
    } @else if (animation) {
      <h1>Animation #{{ animation.id }}</h1>

      <mat-card>
        <mat-card-content>
          <p><strong>Status:</strong> {{ animation.status }}</p>
          <p><strong>Frames:</strong> {{ animation.start_frame }} - {{ animation.end_frame }}</p>
          <p><strong>Progress:</strong></p>
          <mat-progress-bar mode="determinate" [value]="animation.progress" />
          <p>{{ animation.progress }}% complete</p>
          <p><strong>Created:</strong> {{ animation.created_at | date:'medium' }}</p>
        </mat-card-content>
      </mat-card>

      <h2>Per-Frame Status</h2>
      <p>Frame-level status details will be displayed here once the API
         returns per-frame information.</p>
    } @else {
      <p>Animation not found.</p>
    }
  `,
  styles: [`
    mat-card { margin-top: 16px; }
    h2 { margin-top: 24px; }
  `],
})
export class AnimationDetailComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly animationService = inject(AnimationService);
  private sub?: Subscription;

  animation: Animation | null = null;
  loading = true;

  ngOnInit(): void {
    this.sub = this.route.paramMap.pipe(
      switchMap(params =>
        this.animationService.pollDetail(Number(params.get('id')))
      ),
    ).subscribe({
      next: (anim) => { this.animation = anim; this.loading = false; },
      error: () => { this.loading = false; },
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }
}
