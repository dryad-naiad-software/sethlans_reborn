// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Routes } from '@angular/router';

export const SETUP_ROUTES: Routes = [
  {
    path: 'bootstrap-error',
    loadComponent: () =>
      import('./pages/bootstrap-error/bootstrap-error.component').then(
        m => m.BootstrapErrorComponent,
      ),
  },
  {
    path: '',
    loadComponent: () =>
      import('./setup.component').then(m => m.SetupComponent),
  },
];
