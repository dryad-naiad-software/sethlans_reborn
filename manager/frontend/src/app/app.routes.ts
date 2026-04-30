// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Routes } from '@angular/router';
import { adminGuard } from './core/guards/admin.guard';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./layout/layout.component').then(m => m.LayoutComponent),
    canActivate: [authGuard],
    children: [
      {
        path: '',
        loadComponent: () =>
          import('./features/dashboard/dashboard.component')
            .then(m => m.DashboardComponent),
      },
      {
        path: 'projects',
        loadComponent: () =>
          import('./features/projects/project-list.component')
            .then(m => m.ProjectListComponent),
      },
      {
        path: 'projects/:id',
        loadComponent: () =>
          import('./features/projects/project-detail.component')
            .then(m => m.ProjectDetailComponent),
      },
      {
        path: 'workers',
        loadComponent: () =>
          import('./features/workers/worker-list.component')
            .then(m => m.WorkerListComponent),
      },
      {
        path: 'settings',
        loadComponent: () =>
          import('./features/settings/settings.component')
            .then(m => m.SettingsComponent),
      },
      {
        path: 'admin/system-status',
        canActivate: [adminGuard],
        loadChildren: () =>
          import('./features/admin/system-status/system-status.routes')
            .then(m => m.SYSTEM_STATUS_ROUTES),
      },
    ],
  },
  {
    path: 'login',
    loadComponent: () =>
      import('./features/login/login.component')
        .then(m => m.LoginComponent),
  },
  {
    path: 'setup',
    loadChildren: () =>
      import('./features/setup/setup.routes').then(m => m.SETUP_ROUTES),
  },
  { path: '**', redirectTo: '' },
];
