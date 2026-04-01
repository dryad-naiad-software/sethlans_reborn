// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [
    FormsModule, MatCardModule, MatFormFieldModule,
    MatInputModule, MatButtonModule,
  ],
  template: `
    <div class="login-container">
      <mat-card class="login-card">
        <mat-card-header>
          <mat-card-title>Sethlans Manager Login</mat-card-title>
        </mat-card-header>
        <mat-card-content>
          <form (ngSubmit)="onLogin()">
            <mat-form-field class="full-width">
              <mat-label>Username</mat-label>
              <input matInput [(ngModel)]="username" name="username" />
            </mat-form-field>
            <mat-form-field class="full-width">
              <mat-label>Password</mat-label>
              <input matInput type="password" [(ngModel)]="password" name="password" />
            </mat-form-field>
            <button mat-raised-button color="primary" type="submit" class="full-width">
              Login
            </button>
          </form>
          <p class="hint">
            Authentication is not yet implemented. Click login to proceed.
          </p>
        </mat-card-content>
      </mat-card>
    </div>
  `,
  styles: [`
    .login-container {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 80vh;
    }
    .login-card {
      width: 400px;
      padding: 24px;
    }
    .full-width { width: 100%; }
    .hint {
      margin-top: 16px;
      text-align: center;
      color: #999;
      font-size: 13px;
    }
  `],
})
export class LoginComponent {
  username = '';
  password = '';

  constructor(private readonly router: Router) {}

  onLogin(): void {
    // Stub: no real authentication, just navigate to dashboard
    this.router.navigate(['/']);
  }
}
