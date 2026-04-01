// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Component, inject } from '@angular/core';
import { AsyncPipe } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive, Router } from '@angular/router';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatListModule } from '@angular/material/list';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { AuthService } from './core/services/auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    AsyncPipe,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatToolbarModule,
    MatSidenavModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
  ],
  template: `
    <mat-toolbar color="primary">
      <button mat-icon-button (click)="sidenav.toggle()">
        <mat-icon>menu</mat-icon>
      </button>
      <span>Sethlans Manager</span>
      <span class="toolbar-spacer"></span>
      @if (authService.isAuthenticated$ | async) {
        <button mat-icon-button (click)="onLogout()" aria-label="Logout">
          <mat-icon>logout</mat-icon>
        </button>
      }
    </mat-toolbar>

    <mat-sidenav-container class="sidenav-container">
      <mat-sidenav #sidenav mode="side" opened class="sidenav">
        <mat-nav-list>
          <a mat-list-item routerLink="/" routerLinkActive="active"
             [routerLinkActiveOptions]="{exact: true}">
            <mat-icon matListItemIcon>dashboard</mat-icon>
            <span matListItemTitle>Dashboard</span>
          </a>
          <a mat-list-item routerLink="/projects" routerLinkActive="active">
            <mat-icon matListItemIcon>folder</mat-icon>
            <span matListItemTitle>Projects</span>
          </a>
          <a mat-list-item routerLink="/assets" routerLinkActive="active">
            <mat-icon matListItemIcon>attach_file</mat-icon>
            <span matListItemTitle>Assets</span>
          </a>
          <a mat-list-item routerLink="/jobs" routerLinkActive="active">
            <mat-icon matListItemIcon>work</mat-icon>
            <span matListItemTitle>Jobs</span>
          </a>
          <a mat-list-item routerLink="/animations" routerLinkActive="active">
            <mat-icon matListItemIcon>movie</mat-icon>
            <span matListItemTitle>Animations</span>
          </a>
          <a mat-list-item routerLink="/tiled-jobs" routerLinkActive="active">
            <mat-icon matListItemIcon>grid_view</mat-icon>
            <span matListItemTitle>Tiled Jobs</span>
          </a>
          <a mat-list-item routerLink="/workers" routerLinkActive="active">
            <mat-icon matListItemIcon>computer</mat-icon>
            <span matListItemTitle>Workers</span>
          </a>
          <a mat-list-item routerLink="/settings" routerLinkActive="active">
            <mat-icon matListItemIcon>settings</mat-icon>
            <span matListItemTitle>Settings</span>
          </a>
        </mat-nav-list>
      </mat-sidenav>

      <mat-sidenav-content class="content">
        <router-outlet />
      </mat-sidenav-content>
    </mat-sidenav-container>
  `,
  styles: [`
    .sidenav-container {
      height: calc(100vh - 64px);
    }
    .sidenav {
      width: 220px;
    }
    .content {
      padding: 24px;
    }
    .active {
      background-color: rgba(0, 0, 0, 0.04);
    }
    .toolbar-spacer {
      flex: 1 1 auto;
    }
  `],
})
export class AppComponent {
  readonly authService = inject(AuthService);
  private readonly router = inject(Router);

  title = 'Sethlans Manager';

  onLogout(): void {
    this.authService.logout().subscribe({
      next: () => this.router.navigate(['/login']),
      error: () => this.router.navigate(['/login']),
    });
  }
}
