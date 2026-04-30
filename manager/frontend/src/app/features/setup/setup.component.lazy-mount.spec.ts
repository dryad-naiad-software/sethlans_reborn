// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of, NEVER } from 'rxjs';
import { SetupComponent } from './setup.component';
import { SetupApiService } from './services/setup-api.service';
import { SetupStatus } from './models/setup.models';

/**
 * Tests for issue #124 — verify the SetupComponent does NOT eagerly
 * instantiate step child components (and thus does NOT fire their
 * ngOnInit side-effects) before the user navigates to that step.
 *
 * Lives in a sibling spec so the primary setup.component.spec.ts can stay
 * under the 250-line TS cap while every required acceptance case from the
 * issue gets its own focused `it` block.
 */

const API_METHODS = [
  'getStatus', 'setTopology', 'configureNetwork', 'configureDatabase',
  'createAdminUser', 'setWorkerPassword', 'startBlenderDownload',
  'getBlenderProgress', 'cancelBlenderDownload', 'verify', 'getSummary',
  'getHealth', 'requestRestart',
] as const;

describe('SetupComponent — lazy step content mounting (issue #124)', () => {
  let component: SetupComponent;
  let fixture: ComponentFixture<SetupComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;

  const freshStatus: SetupStatus = {
    complete: false, topology: null,
    current_step: null, checkpoints: [],
  };

  beforeEach(() => sessionStorage.clear());

  function buildModule(status: SetupStatus = freshStatus) {
    mockApi = jasmine.createSpyObj('SetupApiService', [...API_METHODS]);
    mockApi.getStatus.and.returnValue(of(status));
    // NEVER so subscribers stay open without resolving — we only care
    // whether the call was made, not what it returned.
    mockApi.startBlenderDownload.and.returnValue(NEVER);
    mockApi.verify.and.returnValue(NEVER);
    mockApi.getSummary.and.returnValue(NEVER);

    return TestBed.configureTestingModule({
      imports: [SetupComponent, NoopAnimationsModule],
      providers: [{ provide: SetupApiService, useValue: mockApi }],
    }).compileComponents();
  }

  async function renderFresh() {
    await buildModule();
    fixture = TestBed.createComponent(SetupComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  describe('fresh start — no premature side-effects', () => {
    it('starts on welcome step without firing later step side-effects',
      async () => {
        await renderFresh();
        expect(component.activeStepKey).toBe('welcome');
      });

    it('does NOT call verify before user reaches verification step',
      async () => {
        await renderFresh();
        expect(mockApi.verify).not.toHaveBeenCalled();
      });

    it('does NOT call startBlenderDownload before user reaches blender step',
      async () => {
        await renderFresh();
        expect(mockApi.startBlenderDownload).not.toHaveBeenCalled();
      });

    it('does NOT call getSummary before user reaches done step',
      async () => {
        await renderFresh();
        expect(mockApi.getSummary).not.toHaveBeenCalled();
      });
  });

  describe('gate flips on at the right time', () => {
    it('mounts verification step and fires verify exactly once ' +
       'when activeStepKey flips to verification', async () => {
      await renderFresh();
      expect(mockApi.verify).not.toHaveBeenCalled();

      component.activeStepKey = 'verification';
      fixture.detectChanges();
      await fixture.whenStable();

      expect(mockApi.verify).toHaveBeenCalledTimes(1);
    });
  });

  describe('resume mid-wizard', () => {
    it('mounts the resume-target step (verification) and fires its ' +
       'side-effect exactly once on first paint', async () => {
      const status: SetupStatus = {
        complete: false, topology: 'manager',
        current_step: 'verified',
        checkpoints: [
          'topology_chosen', 'network_configured',
          'database_configured', 'admin_created',
        ],
      };
      await buildModule(status);
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      await fixture.whenStable();

      expect(component.activeStepKey).toBe('verification');
      expect(mockApi.verify).toHaveBeenCalledTimes(1);
      // Other gated step side-effects must still NOT have fired —
      // only the resume-target step is mounted, not every later step.
      expect(mockApi.getSummary).not.toHaveBeenCalled();
      expect(mockApi.startBlenderDownload).not.toHaveBeenCalled();
    });
  });
});
