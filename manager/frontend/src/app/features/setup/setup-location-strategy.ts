// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable } from '@angular/core';
import { PathLocationStrategy } from '@angular/common';

const SETUP = '/setup';

/**
 * The app's location strategy: Angular's own, except that the setup
 * page's address keeps its trailing slash. Setup mode serves the page at
 * `/setup/` only (FR-SM-3); without this the router rewrites the address
 * to `/setup`, and a reload of the page would get setup mode's 404.
 */
@Injectable({ providedIn: 'root' })
export class SetupPathLocationStrategy extends PathLocationStrategy {
  override prepareExternalUrl(internal: string): string {
    const url = super.prepareExternalUrl(internal);
    const rest = url.slice(SETUP.length);
    if (url.startsWith(SETUP) && (rest === '' || rest.startsWith('?') || rest.startsWith('#'))) {
      return `${SETUP}/${rest}`;
    }
    return url;
  }
}
