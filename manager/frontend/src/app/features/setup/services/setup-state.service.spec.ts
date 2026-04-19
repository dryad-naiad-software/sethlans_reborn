// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { SetupStateService } from './setup-state.service';
import { SetupStatus } from '../models/setup.models';

const LEGACY_KEY = 'sethlans.setupToken';

describe('SetupStateService', () => {
  let service: SetupStateService;

  beforeEach(() => {
    sessionStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({});
    service = TestBed.inject(SetupStateService);
  });

  afterEach(() => sessionStorage.clear());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('legacy token cleanup', () => {
    it('removes legacy sethlans.setupToken key on construction', () => {
      sessionStorage.setItem(LEGACY_KEY, 'stale-token');
      TestBed.resetTestingModule();
      TestBed.configureTestingModule({});
      const removeSpy = spyOn(sessionStorage, 'removeItem').and.callThrough();
      TestBed.inject(SetupStateService);
      expect(removeSpy).toHaveBeenCalledWith(LEGACY_KEY);
      expect(sessionStorage.getItem(LEGACY_KEY)).toBeNull();
    });

    it('does not expose a setupToken field', () => {
      expect((service as unknown as Record<string, unknown>)['setupToken'])
        .toBeUndefined();
    });
  });

  describe('setTopology', () => {
    it('updates topology', () => {
      service.setTopology('manager');
      expect(service.topology).toBe('manager');
    });

    it('recomputes visible steps for manager', () => {
      service.setTopology('manager');
      expect(service.visibleSteps.length).toBe(8);
    });

    it('recomputes visible steps for manager_worker', () => {
      service.setTopology('manager_worker');
      expect(service.visibleSteps.length).toBe(10);
    });

    it('recomputes visible steps for worker_only', () => {
      service.setTopology('worker_only');
      expect(service.visibleSteps.length).toBe(8);
    });
  });

  describe('adminPassword', () => {
    it('stores and retrieves admin password', () => {
      service.setAdminPassword('secret123');
      expect(service.getAdminPassword()).toBe('secret123');
    });

    it('returns null when no password is set', () => {
      expect(service.getAdminPassword()).toBeNull();
    });
  });

  describe('clearSensitiveData', () => {
    it('clears admin password', () => {
      service.setAdminPassword('secret123');
      service.clearSensitiveData();
      expect(service.getAdminPassword()).toBeNull();
    });
  });

  describe('markCheckpoint', () => {
    it('adds a checkpoint', () => {
      service.markCheckpoint('topology_chosen');
      expect(service.checkpoints).toContain('topology_chosen');
    });

    it('is idempotent', () => {
      service.markCheckpoint('topology_chosen');
      service.markCheckpoint('topology_chosen');
      expect(service.checkpoints.filter(c => c === 'topology_chosen').length).toBe(1);
    });

    it('accumulates multiple checkpoints', () => {
      service.markCheckpoint('topology_chosen');
      service.markCheckpoint('network_configured');
      expect(service.checkpoints).toEqual(['topology_chosen', 'network_configured']);
    });
  });

  describe('resumeFromStatus', () => {
    it('restores topology and visible steps', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager_worker',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      service.resumeFromStatus(status);
      expect(service.topology).toBe('manager_worker');
      expect(service.visibleSteps.length).toBe(10);
    });

    it('restores checkpoints', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      service.resumeFromStatus(status);
      expect(service.checkpoints).toEqual(['topology_chosen', 'network_configured']);
    });

    it('returns index of first incomplete step', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      const idx = service.resumeFromStatus(status);
      expect(idx).toBe(3);
    });

    it('returns last step index when all checkpoints complete', () => {
      const status: SetupStatus = {
        complete: true,
        topology: 'manager',
        current_step: null,
        checkpoints: [
          'topology_chosen', 'network_configured', 'database_configured',
          'admin_created', 'ffmpeg_installed', 'verified',
        ],
      };
      const idx = service.resumeFromStatus(status);
      expect(idx).toBe(service.visibleSteps.length - 1);
    });

    it('does not clobber topology when status topology is null', () => {
      service.setTopology('manager');
      const status: SetupStatus = {
        complete: false,
        topology: null,
        current_step: null,
        checkpoints: [],
      };
      service.resumeFromStatus(status);
      expect(service.topology).toBe('manager');
    });

    it('returns 1 when welcome done and topology not checkpointed', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: [],
      };
      const idx = service.resumeFromStatus(status);
      expect(idx).toBe(1);
    });
  });
});
