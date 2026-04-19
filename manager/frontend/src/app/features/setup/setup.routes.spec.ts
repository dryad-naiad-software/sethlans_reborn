// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Route } from '@angular/router';
import { SETUP_ROUTES } from './setup.routes';
import { setupSessionGuard } from './guards/setup-session.guard';

/**
 * Route-table assertions (FR-7, FR-9).
 *
 *  - `/setup`            -> TokenEntryComponent
 *  - `/setup/wizard`     -> SetupComponent guarded by setupSessionGuard
 *  - `/setup/bootstrap-error` does NOT exist in the table.
 */
describe('SETUP_ROUTES (FR-7 / FR-9)', () => {
  it('is a non-empty route table', () => {
    expect(Array.isArray(SETUP_ROUTES)).toBeTrue();
    expect(SETUP_ROUTES.length).toBeGreaterThan(0);
  });

  it('declares no /setup/bootstrap-error route', () => {
    const paths = SETUP_ROUTES.map(r => r.path);
    expect(paths).not.toContain('bootstrap-error');
  });

  describe('root (/setup)', () => {
    let root: Route | undefined;

    beforeAll(() => {
      root = SETUP_ROUTES.find(r => r.path === '');
    });

    it('exists with empty path', () => {
      expect(root).toBeDefined();
    });

    it('lazy-loads TokenEntryComponent', async () => {
      expect(root?.loadComponent).toBeDefined();
      const mod = await (root!.loadComponent as () => Promise<unknown>)();
      // Karma/minification may append a digit suffix to class .name; match
      // the stable prefix instead of exact equality.
      expect((mod as { name: string }).name).toMatch(/^TokenEntryComponent/);
    });

    it('has no canActivate guard on the token entry route', () => {
      expect(root?.canActivate ?? []).toEqual([]);
    });
  });

  describe('wizard (/setup/wizard)', () => {
    let wizard: Route | undefined;

    beforeAll(() => {
      wizard = SETUP_ROUTES.find(r => r.path === 'wizard');
    });

    it('exists with path "wizard"', () => {
      expect(wizard).toBeDefined();
    });

    it('lazy-loads SetupComponent', async () => {
      expect(wizard?.loadComponent).toBeDefined();
      const mod = await (wizard!.loadComponent as () => Promise<unknown>)();
      expect((mod as { name: string }).name).toMatch(/^SetupComponent/);
    });

    it('is guarded by setupSessionGuard', () => {
      expect(wizard?.canActivate).toBeDefined();
      expect(wizard?.canActivate).toContain(setupSessionGuard);
    });
  });
});
