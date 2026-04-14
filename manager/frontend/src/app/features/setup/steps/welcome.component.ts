// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, output } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-setup-welcome',
  standalone: true,
  imports: [MatButtonModule, MatIconModule],
  template: `
    <div class="welcome-container">
      <mat-icon class="welcome-icon">blur_on</mat-icon>
      <h1>Welcome to Sethlans</h1>
      <p class="subtitle">Distributed Blender Rendering</p>
      <p class="description">
        This wizard will guide you through configuring your Sethlans manager.
        You will set up networking, database, admin account, and required tools.
      </p>
      <button mat-raised-button color="primary" (click)="stepComplete.emit()">
        Get Started
      </button>
    </div>
  `,
  styles: [`
    .welcome-container {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      padding: 48px 24px;
    }
    .welcome-icon {
      font-size: 72px;
      width: 72px;
      height: 72px;
      color: #1976d2;
      margin-bottom: 16px;
    }
    h1 { margin: 0 0 8px; }
    .subtitle {
      font-size: 18px;
      color: rgba(0, 0, 0, 0.6);
      margin: 0 0 24px;
    }
    .description {
      max-width: 480px;
      color: rgba(0, 0, 0, 0.7);
      margin-bottom: 32px;
      line-height: 1.5;
    }
  `],
})
export class WelcomeComponent {
  readonly stepComplete = output<void>();
}
