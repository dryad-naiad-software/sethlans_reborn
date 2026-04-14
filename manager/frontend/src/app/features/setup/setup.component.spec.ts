// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { ActivatedRoute, convertToParamMap } from '@angular/router';
import { of, throwError, NEVER } from 'rxjs';
import { SetupComponent } from './setup.component';
import { SetupApiService } from './services/setup-api.service';
import { SetupStateService } from './services/setup-state.service';
import { SetupStatus } from './models/setup.models';

/** All methods on SetupApiService that child components may call. */
const API_METHODS = [
  'getStatus', 'setTopology', 'configureNetwork', 'configureDatabase',
  'createAdminUser', 'setWorkerPassword', 'startFfmpegDownload',
  'getFfmpegProgress', 'cancelFfmpegDownload', 'startBlenderDownload',
  'getBlenderProgress', 'cancelBlenderDownload', 'verify', 'getSummary',
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

  function buildModule(
    queryParams: Record<string, string> = {},
    status: SetupStatus = freshStatus,
  ) {
    mockApi = jasmine.createSpyObj('SetupApiService', [...API_METHODS]);
    mockApi.getStatus.and.returnValue(of(status));
    // Provide NEVER for methods child components call on init to prevent
    // unexpected emissions during SetupComponent-level tests.
    mockApi.startFfmpegDownload.and.returnValue(NEVER);
    mockApi.startBlenderDownload.and.returnValue(NEVER);
    mockApi.verify.and.returnValue(NEVER);
    mockApi.getSummary.and.returnValue(NEVER);

    return TestBed.configureTestingModule({
      imports: [SetupComponent, NoopAnimationsModule],
      providers: [
        { provide: SetupApiService, useValue: mockApi },
        {
          provide: ActivatedRoute,
          useValue: {
            snapshot: { queryParamMap: convertToParamMap(queryParams) },
          },
        },
      ],
    }).compileComponents();
  }

  describe('initialization', () => {
    it('should read token from query params', async () => {
      await buildModule({ token: 'my-setup-token' });
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      state = TestBed.inject(SetupStateService);
      fixture.detectChanges();
      expect(state.setupToken).toBe('my-setup-token');
    });

    it('should not set token when none in query params', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      state = TestBed.inject(SetupStateService);
      fixture.detectChanges();
      expect(state.setupToken).toBeNull();
    });

    it('should call getStatus on init', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(mockApi.getStatus).toHaveBeenCalled();
    });

    it('should set loading to false after getStatus returns', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      expect(component.loading).toBeTrue();
      fixture.detectChanges();
      expect(component.loading).toBeFalse();
    });

    it('should populate steps from state on fresh start', async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();
      expect(component.steps.length).toBe(8);
    });
  });

  describe('resume from status', () => {
    it('should resume from checkpoints and mark prior steps completed', async () => {
      const status: SetupStatus = {
        complete: false, topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      await buildModule({}, status);
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
    it('should set loading to false on getStatus error', async () => {
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
    it('should mark step as completed', fakeAsync(async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      fixture.detectChanges();

      const step = component.steps[0];
      component.onStepComplete(step);
      tick();
      expect(component.isStepCompleted(step)).toBeTrue();
    }));

    it('should refresh steps after topology step', fakeAsync(async () => {
      await buildModule();
      fixture = TestBed.createComponent(SetupComponent);
      component = fixture.componentInstance;
      state = TestBed.inject(SetupStateService);
      fixture.detectChanges();

      state.setTopology('manager_worker');
      const topoStep = {
        key: 'topology', label: 'Topology', checkpoint: 'topology_chosen',
      };
      component.onStepComplete(topoStep);
      tick();
      expect(component.steps.length).toBe(10);
    }));
  });
});
