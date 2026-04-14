// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { getStepsForTopology, StepConfig } from './setup-step-config';

describe('getStepsForTopology', () => {
  function stepKeys(steps: StepConfig[]): string[] {
    return steps.map(s => s.key);
  }

  it('should return 8 steps for manager topology', () => {
    const steps = getStepsForTopology('manager');
    expect(steps.length).toBe(8);
    expect(stepKeys(steps)).toEqual([
      'welcome', 'topology', 'network', 'database',
      'admin-user', 'ffmpeg-download', 'verification', 'done',
    ]);
  });

  it('should return 10 steps for manager_worker topology', () => {
    const steps = getStepsForTopology('manager_worker');
    expect(steps.length).toBe(10);
    expect(stepKeys(steps)).toEqual([
      'welcome', 'topology', 'network', 'database',
      'admin-user', 'worker-password', 'ffmpeg-download',
      'blender-download', 'verification', 'done',
    ]);
  });

  it('should return 8 steps for worker_only topology', () => {
    const steps = getStepsForTopology('worker_only');
    expect(steps.length).toBe(8);
    expect(stepKeys(steps)).toEqual([
      'welcome', 'topology', 'network', 'database',
      'admin-user', 'ffmpeg-download', 'verification', 'done',
    ]);
  });

  it('should return 8 steps for null topology (default)', () => {
    const steps = getStepsForTopology(null);
    expect(steps.length).toBe(8);
    expect(stepKeys(steps)).toEqual([
      'welcome', 'topology', 'network', 'database',
      'admin-user', 'ffmpeg-download', 'verification', 'done',
    ]);
  });

  it('should not include worker-password for manager topology', () => {
    const steps = getStepsForTopology('manager');
    expect(stepKeys(steps)).not.toContain('worker-password');
  });

  it('should not include blender-download for manager topology', () => {
    const steps = getStepsForTopology('manager');
    expect(stepKeys(steps)).not.toContain('blender-download');
  });

  it('should include worker-password for manager_worker topology', () => {
    const steps = getStepsForTopology('manager_worker');
    expect(stepKeys(steps)).toContain('worker-password');
  });

  it('should include blender-download for manager_worker topology', () => {
    const steps = getStepsForTopology('manager_worker');
    expect(stepKeys(steps)).toContain('blender-download');
  });

  it('should have correct checkpoint names', () => {
    const steps = getStepsForTopology('manager_worker');
    const checkpointMap = new Map(steps.map(s => [s.key, s.checkpoint]));
    expect(checkpointMap.get('welcome')).toBeNull();
    expect(checkpointMap.get('topology')).toBe('topology_chosen');
    expect(checkpointMap.get('network')).toBe('network_configured');
    expect(checkpointMap.get('database')).toBe('database_configured');
    expect(checkpointMap.get('admin-user')).toBe('admin_created');
    expect(checkpointMap.get('worker-password')).toBe('worker_password_set');
    expect(checkpointMap.get('ffmpeg-download')).toBe('ffmpeg_installed');
    expect(checkpointMap.get('blender-download')).toBe('blender_predownloaded');
    expect(checkpointMap.get('verification')).toBe('verified');
    expect(checkpointMap.get('done')).toBeNull();
  });
});
