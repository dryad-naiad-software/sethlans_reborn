// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { APP_BASE_HREF } from '@angular/common';
import { SetupPathLocationStrategy } from './setup-location-strategy';

describe('SetupPathLocationStrategy', () => {
  /** FR-SM-3, FR-WZ-10: setup mode serves /setup/ only, so that address keeps its slash; no other does. */
  const urls: [string, string][] = [
    ['/setup', '/setup/'],
    ['/setup?x=1', '/setup/?x=1'],
    ['/setup#top', '/setup/#top'],
    ['/setup/', '/setup/'],
    ['/setupx', '/setupx'],
    ['/login', '/login'],
  ];
  for (const [internal, external] of urls) {
    it(`prepares ${internal} as ${external}`, () => {
      TestBed.configureTestingModule({ providers: [{ provide: APP_BASE_HREF, useValue: '/' }] });
      expect(TestBed.inject(SetupPathLocationStrategy).prepareExternalUrl(internal)).toBe(external);
    });
  }
});
