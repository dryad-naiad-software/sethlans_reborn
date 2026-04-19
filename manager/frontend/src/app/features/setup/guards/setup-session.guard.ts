// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { inject } from '@angular/core';
import { CanActivateFn, Router, UrlTree } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { SetupApiService } from '../services/setup-api.service';

/**
 * Gate for `/setup/wizard`. Probes `GET /api/setup/session/`:
 *  - 204 → return true (session bound, proceed).
 *  - 403 `setup_in_progress` → UrlTree('/setup') (bounce user to token entry).
 *  - Any other error (5xx, network 0, unexpected) → UrlTree('/setup').
 *
 * The token-entry page surfaces any connectivity issue; this guard's job is
 * purely to avoid loading the wizard when no session is bound.
 */
export const setupSessionGuard: CanActivateFn = async (): Promise<
  boolean | UrlTree
> => {
  const api = inject(SetupApiService);
  const router = inject(Router);
  try {
    await firstValueFrom(api.getSetupSession());
    return true;
  } catch {
    return router.createUrlTree(['/setup']);
  }
};
