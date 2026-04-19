// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { of, throwError, NEVER } from 'rxjs';
import { SetupComponent } from './setup.component';
import { SetupApiService } from './services/setup-api.service';
import { SetupStateService } from './services/setup-state.service';
import { SetupStatus } from './models/setup.models';

const API_METHODS = [
  'getStatus', 'setTopology', 'configureNetwork', 'configureDatabase',
  'createAdminUser', 'setWorkerPassword', 'startFfmpegDownload',
  'getFfmpegProgress', 'cancelFfmpegDownload', 'startBlenderDownload',
  'getBlenderProgress', 'cancelBlenderDownload', 'verify', 'getSummary',
  'getHealth', 'requestRestart',
] as const;

describe('SetupComponent', () => {
  let component: SetupComponent;
  let fixture: ComponentFixture<SetupComponent>;
  let mockApi: jasmine.SpyObj<SetupApiService>;
  let state: SetupStateService;

  const freshStatus: SetupStatus = {
    complete: false, topology: null,
    current_step: null, checkpoints: [],
  };

  beforeEach(() => sessionStorage.clear());

  function buildModule(status: SetupStatus = freshStatus) {
    mockApi = jasmine.createSpyObj('SetupApiService', [...API_METHODS]);
    mockApi.getStatus.and.returnValue(of(status));
    mockApi.startFfmpegDownload.and.returnValue(NEVER);
    mockApi.startBlenderDownload.and.returnValue(NEVER);
    mockApi.verify.and.returnValue(NEVER);
    mockApi.getSummary.and.returnValue(NEVER);

    return TestBed.configureTestingModule({
      imports: [SetupComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
      ],
    }).compileComponents();
  }

  describe('initialization', () => {
    it('calls getStatus on init', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(mockApi.getStatus).toHaveBeenCalled();
    });

    it('sets loading to false after getStatus returns', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      expect(component.loading).toBeTrue();
      fixture.detectChanges();
      expect(component.loading).toBeFalse();
    });

    it('populates steps from state on fresh start', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.steps.length).toBe(8);
    });

    it('does NOT read setup token from query params (moved to APP_INITIALIZER)',
      async () => {
        await buildModule();
        fixture = TestBed.createComponent(SetupComponent);
        component = fixture.componentInstance;
        state = TestBed.inject(SetupStateService);
        fixture.detectChanges();
        // setupToken field was removed from SetupStateService.
        expect((state as unknown as Record<string, unknown>)['setupToken'])
          .toBeUndefined();
      });
  });

  describe('resume from status', () => {
    it('resumes from checkpoints and marks prior steps completed', async () => {
      const status: SetupStatus = {
        complete: false, topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      await buildModule(status);
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();

      expect(component.isStepCompleted(
        { key: 'welcome', label: '', checkpoint: null })).toBeTrue();
      expect(component.isStepCompleted(
        { key: 'topology', label: '', checkpoint: 'topology_chosen' })).toBeTrue();
      expect(component.isStepCompleted(
        { key: 'network', label: '', checkpoint: 'network_configured' })).toBeTrue();
    });
  });

  describe('error handling', () => {
    it('sets loading to false on getStatus error', async () => {
      await buildModule();
      mockApi.getStatus.and.returnValue(throwError(() => new Error('fail')));
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.loading).toBeFalse();
      expect(component.steps.length).toBeGreaterThan(0);
    });
  });

  describe('onStepComplete', () => {
    it('marks step as completed', fakeAsync(async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();

      const step = component.steps[0];
      component.onStepComplete(step);
      tick();
      expect(component.isStepCompleted(step)).toBeTrue();
    }));

    it('does NOT call clearSetupToken on verification complete (method removed)',
      fakeAsync(async () => {
        await buildModule();
        fixture = TestBed.createComponent(SetupComponent);
        component = fixture.componentInstance;
        state = TestBed.inject(SetupStateService);
        fixture.detectChanges();

        // Verify method does not exist on the service.
        expect(
          (state as unknown as Record<string, unknown>)['clearSetupToken'],
        ).toBeUndefined();

        // Complete verification step and assert component does not throw.
        component.onStepComplete({
          key: 'verification', label: 'Verification', checkpoint: null,
        });
        tick();
        expect(component.isStepCompleted(
          { key: 'verification', label: '', checkpoint: null }))
          .toBeTrue();
      }));

    it('refreshes steps after topology step', fakeAsync(async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      state = TestBed.inject(SetupStateService);
      fixture.detectChanges();

      state.setTopology('manager_worker');
      component.onStepComplete({
        key: 'topology', label: 'Topology', checkpoint: 'topology_chosen',
      });
      tick();
      expect(component.steps.length).toBe(10);
    }));
  });
});
