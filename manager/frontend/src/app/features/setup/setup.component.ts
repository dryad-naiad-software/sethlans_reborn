// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject, OnInit, ViewChild } from '@angular/core';
import { MatStepper, MatStepperModule } from '@angular/material/stepper';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import {
  STEPPER_GLOBAL_OPTIONS,
  StepperSelectionEvent,
} from '@angular/cdk/stepper';
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
        <mat-stepper #stepper linear labelPosition="bottom" class="setup-stepper"
                     (selectionChange)="onStepperSelectionChange($event)">
          @for (step of steps; track step.key) {
            <mat-step [label]="step.label" [completed]="isStepCompleted(step)"
                      [editable]="false">
              <div class="step-content">
                <!--
                  Each @case body is gated behind activeStepKey so that the
                  contained component is only instantiated when its step is
                  the currently-selected step in the stepper. Without this
                  gate, mat-stepper materializes every step's content on
                  load, which would fire ngOnInit side-effects (e.g.
                  ffmpeg/blender downloads, verify, summary) before the
                  user navigates to those steps. See issue #124.
                -->
                @switch (step.key) {
                  @case ('welcome') {
                    @if (activeStepKey === 'welcome') {
                      <app-setup-welcome (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('topology') {
                    @if (activeStepKey === 'topology') {
                      <app-setup-topology (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('network') {
                    @if (activeStepKey === 'network') {
                      <app-setup-network (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('database') {
                    @if (activeStepKey === 'database') {
                      <app-setup-database (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('admin-user') {
                    @if (activeStepKey === 'admin-user') {
                      <app-setup-admin-user (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('worker-password') {
                    @if (activeStepKey === 'worker-password') {
                      <app-setup-worker-password
                        (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('ffmpeg-download') {
                    @if (activeStepKey === 'ffmpeg-download') {
                      <app-setup-ffmpeg-download
                        (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('blender-download') {
                    @if (activeStepKey === 'blender-download') {
                      <app-setup-blender-download
                        (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('verification') {
                    @if (activeStepKey === 'verification') {
                      <app-setup-verification
                        (stepComplete)="onStepComplete(step)" />
                    }
                  }
                  @case ('done') {
                    @if (activeStepKey === 'done') {
                      <app-setup-done />
                    }
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

  private readonly api = inject(SetupApiService);
  readonly state = inject(SetupStateService);
  private readonly snackBar = inject(MatSnackBar);

  loading = true;
  steps: StepConfig[] = [];
  /**
   * Key of the step whose content is currently mounted. Only the
   * matching @case body in the template instantiates its child
   * component; all other steps render nothing in their content area
   * (the stepper header still shows every step). This prevents step
   * components from running ngOnInit side-effects (download starts,
   * verify, summary fetches) before the user navigates to them.
   * See issue #124.
   */
  activeStepKey: string | null = null;
  private completedStepKeys = new Set<string>();

  ngOnInit(): void {
    // Token handling is performed in APP_INITIALIZER (see app.config.ts).
    this.api.getStatus().subscribe({
      next: (status) => {
        if (status.checkpoints.length > 0) {
          const resumeIndex = this.state.resumeFromStatus(status);
          this.steps = this.state.visibleSteps;
          this.markPriorStepsCompleted(resumeIndex);
          // Initial active step is the resume target (or first step
          // if resumeIndex === 0); set before stepper materializes so
          // the right child component mounts on first paint.
          this.activeStepKey =
            this.steps[Math.min(resumeIndex, this.steps.length - 1)]?.key
            ?? null;
          this.loading = false;
          // Defer stepper navigation to after view init
          setTimeout(() => {
            if (this.stepper && resumeIndex > 0) {
              this.stepper.selectedIndex = resumeIndex;
            }
          });
        } else {
          this.steps = this.state.visibleSteps;
          this.activeStepKey = this.steps[0]?.key ?? null;
          this.loading = false;
        }
      },
      error: () => {
        this.steps = this.state.visibleSteps;
        this.activeStepKey = this.steps[0]?.key ?? null;
        this.loading = false;
      },
    });
  }

  isStepCompleted(step: StepConfig): boolean {
    return this.completedStepKeys.has(step.key);
  }

  onStepperSelectionChange(event: StepperSelectionEvent): void {
    const next = this.steps[event.selectedIndex];
    this.activeStepKey = next?.key ?? null;
  }

  onStepComplete(step: StepConfig): void {
    this.completedStepKeys.add(step.key);

    // After topology step, steps may have changed
    if (step.key === 'topology') {
      this.steps = this.state.visibleSteps;
    }

    // Advance stepper on next tick so Angular can process the completed state.
    // The stepper's (selectionChange) handler updates activeStepKey, which
    // mounts the next step's content.
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
