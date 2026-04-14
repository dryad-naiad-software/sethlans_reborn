// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, ViewChild } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { MatStepper, MatStepperModule } from '@angular/material/stepper';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { STEPPER_GLOBAL_OPTIONS } from '@angular/cdk/stepper';
import { SetupApiService } from './services/setup-api.service';
import { SetupStateService } from './services/setup-state.service';
import { StepConfig } from './setup-step-config';
import { WelcomeComponent } from './steps/welcome.component';
import { TopologyComponent } from './steps/topology.component';
import { NetworkComponent } from './steps/network.component';
import { DatabaseComponent } from './steps/database.component';
import { AdminUserComponent } from './steps/admin-user.component';
import { WorkerPasswordComponent } from './steps/worker-password.component';
import { FfmpegDownloadComponent } from './steps/ffmpeg-download.component';
import { BlenderDownloadComponent } from './steps/blender-download.component';
import { VerificationComponent } from './steps/verification.component';
import { DoneComponent } from './steps/done.component';

@Component({
  selector: 'app-setup',
  standalone: true,
  imports: [
    MatStepperModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    WelcomeComponent,
    TopologyComponent,
    NetworkComponent,
    DatabaseComponent,
    AdminUserComponent,
    WorkerPasswordComponent,
    FfmpegDownloadComponent,
    BlenderDownloadComponent,
    VerificationComponent,
    DoneComponent,
  ],
  providers: [
    { provide: STEPPER_GLOBAL_OPTIONS, useValue: { showError: true } },
  ],
  template: `
    @if (loading) {
      <div class="loading-container">
        <mat-spinner diameter="48" />
        <p>Checking setup status...</p>
      </div>
    } @else {
      <div class="setup-container">
        <mat-stepper #stepper linear labelPosition="bottom" class="setup-stepper">
          @for (step of steps; track step.key) {
            <mat-step [label]="step.label" [completed]="isStepCompleted(step)"
                      [editable]="false">
              <div class="step-content">
                @switch (step.key) {
                  @case ('welcome') {
                    <app-setup-welcome (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('topology') {
                    <app-setup-topology (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('network') {
                    <app-setup-network (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('database') {
                    <app-setup-database (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('admin-user') {
                    <app-setup-admin-user (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('worker-password') {
                    <app-setup-worker-password
                      (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('ffmpeg-download') {
                    <app-setup-ffmpeg-download
                      (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('blender-download') {
                    <app-setup-blender-download
                      (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('verification') {
                    <app-setup-verification
                      (stepComplete)="onStepComplete(step)" />
                  }
                  @case ('done') {
                    <app-setup-done />
                  }
                }
              </div>
            </mat-step>
          }
        </mat-stepper>
      </div>
    }
  `,
  styles: [`
    .loading-container {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 60vh;
      gap: 16px;
    }
    .loading-container p { color: rgba(0, 0, 0, 0.6); }
    .setup-container {
      max-width: 900px;
      margin: 24px auto;
      padding: 0 16px;
    }
    .step-content { padding: 24px 0; }
    .setup-stepper {
      background: transparent;
    }
  `],
})
export class SetupComponent implements OnInit {
  @ViewChild('stepper') stepper!: MatStepper;

  private readonly route = inject(ActivatedRoute);
  private readonly api = inject(SetupApiService);
  readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  loading = true;
  steps: StepConfig[] = [];
  private completedStepKeys = new Set<string>();

  ngOnInit(): void {
    const token = this.route.snapshot.queryParamMap.get('token');
    if (token) {
      this.state.setSetupToken(token);
    }

    this.api.getStatus().subscribe({
      next: (status) => {
        if (status.checkpoints.length > 0) {
          const resumeIndex = this.state.resumeFromStatus(status);
          this.steps = this.state.visibleSteps;
          this.markPriorStepsCompleted(resumeIndex);
          this.loading = false;
          // Defer stepper navigation to after view init
          setTimeout(() => {
            if (this.stepper && resumeIndex > 0) {
              this.stepper.selectedIndex = resumeIndex;
            }
          });
        } else {
          this.steps = this.state.visibleSteps;
          this.loading = false;
        }
      },
      error: () => {
        this.steps = this.state.visibleSteps;
        this.loading = false;
      },
    });
  }

  isStepCompleted(step: StepConfig): boolean {
    return this.completedStepKeys.has(step.key);
  }

  onStepComplete(step: StepConfig): void {
    this.completedStepKeys.add(step.key);

    // After topology step, steps may have changed
    if (step.key === 'topology') {
      this.steps = this.state.visibleSteps;
    }

    // Advance stepper on next tick so Angular can process the completed state
    setTimeout(() => {
      if (this.stepper) {
        this.stepper.next();
      }
    });
  }

  private markPriorStepsCompleted(upToIndex: number): void {
    for (let i = 0; i < upToIndex && i < this.steps.length; i++) {
      this.completedStepKeys.add(this.steps[i].key);
    }
  }
}
