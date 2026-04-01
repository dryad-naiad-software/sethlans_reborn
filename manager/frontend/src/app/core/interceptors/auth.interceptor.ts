// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { HttpInterceptorFn } from '@angular/common/http';

/**
 * Stubbed auth interceptor. When authentication is implemented,
 * this will attach auth tokens to outgoing requests.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  // TODO: Attach auth token when authentication is enabled
  return next(req);
};
