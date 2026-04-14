// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { TestBed } from '@angular/core/testing';
import { SetupStateService } from './setup-state.service';
import { SetupStatus } from '../models/setup.models';

describe('SetupStateService', () => {
  let service: SetupStateService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(SetupStateService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('setSetupToken', () => {
    it('should store the token', () => {
      service.setSetupToken('abc-token');
      expect(service.setupToken).toBe('abc-token');
    });
  });

  describe('setTopology', () => {
    it('should update topology', () => {
      service.setTopology('manager');
      expect(service.topology).toBe('manager');
    });

    it('should recompute visible steps for manager', () => {
      service.setTopology('manager');
      expect(service.visibleSteps.length).toBe(8);
    });

    it('should recompute visible steps for manager_worker', () => {
      service.setTopology('manager_worker');
      expect(service.visibleSteps.length).toBe(10);
    });

    it('should recompute visible steps for worker_only', () => {
      service.setTopology('worker_only');
      expect(service.visibleSteps.length).toBe(8);
    });
  });

  describe('adminPassword', () => {
    it('should store and retrieve admin password', () => {
      service.setAdminPassword('secret123');
      expect(service.getAdminPassword()).toBe('secret123');
    });

    it('should return null when no password is set', () => {
      expect(service.getAdminPassword()).toBeNull();
    });
  });

  describe('clearSensitiveData', () => {
    it('should clear admin password', () => {
      service.setAdminPassword('secret123');
      service.clearSensitiveData();
      expect(service.getAdminPassword()).toBeNull();
    });
  });

  describe('markCheckpoint', () => {
    it('should add a checkpoint', () => {
      service.markCheckpoint('topology_chosen');
      expect(service.checkpoints).toContain('topology_chosen');
    });

    it('should be idempotent', () => {
      service.markCheckpoint('topology_chosen');
      service.markCheckpoint('topology_chosen');
      expect(service.checkpoints.filter(c => c === 'topology_chosen').length).toBe(1);
    });

    it('should accumulate multiple checkpoints', () => {
      service.markCheckpoint('topology_chosen');
      service.markCheckpoint('network_configured');
      expect(service.checkpoints).toEqual(['topology_chosen', 'network_configured']);
    });
  });

  describe('resumeFromStatus', () => {
    it('should restore topology and visible steps', () => {
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

    it('should restore checkpoints', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      service.resumeFromStatus(status);
      expect(service.checkpoints).toEqual(['topology_chosen', 'network_configured']);
    });

    it('should return index of first incomplete step', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: ['topology_chosen', 'network_configured'],
      };
      const idx = service.resumeFromStatus(status);
      // Steps: welcome(null), topology(topology_chosen), network(network_configured),
      //        database(database_configured) <-- first incomplete
      expect(idx).toBe(3);
    });

    it('should return last step index when all checkpoints complete', () => {
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

    it('should not set topology when status topology is null', () => {
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

    it('should return 0 when welcome has no checkpoint and no others done', () => {
      const status: SetupStatus = {
        complete: false,
        topology: 'manager',
        current_step: null,
        checkpoints: [],
      };
      const idx = service.resumeFromStatus(status);
      // welcome has checkpoint=null so it's skipped; topology has checkpoint
      // 'topology_chosen' which is not in checkpoints -> returns index 1
      expect(idx).toBe(1);
    });
  });
});
