// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Routes } from '@angular/router';

/**
 * Lazy route for the admin "System status" page.
 *
 * The route is registered at `/admin/system-status` in the top-level
 * `app.routes.ts` and gated by `[authGuard, adminGuard]`.
 */
export const SYSTEM_STATUS_ROUTES: Routes = [
  {
    path: '',
    loadComponent: () =>
      import('./system-status.component').then(m => m.SystemStatusComponent),
  },
];
