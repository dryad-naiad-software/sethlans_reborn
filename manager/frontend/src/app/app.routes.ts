// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Routes } from '@angular/router';
import { authGuard } from './core/guards/auth.guard';

export const routes: Routes = [
  {
    path: '',
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
        path: 'assets',
        loadComponent: () =>
          import('./features/assets/asset-list.component')
            .then(m => m.AssetListComponent),
      },
      {
        path: 'jobs',
        loadComponent: () =>
          import('./features/jobs/job-list.component')
            .then(m => m.JobListComponent),
      },
      {
        path: 'jobs/:id',
        loadComponent: () =>
          import('./features/jobs/job-detail.component')
            .then(m => m.JobDetailComponent),
      },
      {
        path: 'animations',
        loadComponent: () =>
          import('./features/animations/animation-list.component')
            .then(m => m.AnimationListComponent),
      },
      {
        path: 'animations/:id',
        loadComponent: () =>
          import('./features/animations/animation-detail.component')
            .then(m => m.AnimationDetailComponent),
      },
      {
        path: 'tiled-jobs',
        loadComponent: () =>
          import('./features/tiled-jobs/tiled-job-list.component')
            .then(m => m.TiledJobListComponent),
      },
      {
        path: 'tiled-jobs/:id',
        loadComponent: () =>
          import('./features/tiled-jobs/tiled-job-detail.component')
            .then(m => m.TiledJobDetailComponent),
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
    ],
  },
  {
    path: 'login',
    loadComponent: () =>
      import('./features/login/login.component')
        .then(m => m.LoginComponent),
  },
  { path: '**', redirectTo: '' },
];
