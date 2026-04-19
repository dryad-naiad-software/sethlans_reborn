// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Routes } from '@angular/router';
import { setupSessionGuard } from './guards/setup-session.guard';

export const SETUP_ROUTES: Routes = [
  {
    path: '',
    pathMatch: 'full',
    loadComponent: () =>
      import('./pages/token-entry/token-entry.component').then(
        m => m.TokenEntryComponent,
      ),
  },
  {
    path: 'wizard',
    canActivate: [setupSessionGuard],
    loadComponent: () =>
      import('./setup.component').then(m => m.SetupComponent),
  },
];
