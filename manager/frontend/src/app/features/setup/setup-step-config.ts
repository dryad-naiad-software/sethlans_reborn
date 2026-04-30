// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Topology } from './models/setup.models';

export interface StepConfig {
  key: string;
  label: string;
  checkpoint: string | null;
}

export function getStepsForTopology(topology: Topology | null): StepConfig[] {
  const steps: StepConfig[] = [
    { key: 'welcome', label: 'Welcome', checkpoint: null },
    { key: 'topology', label: 'Topology', checkpoint: 'topology_chosen' },
    { key: 'network', label: 'Network', checkpoint: 'network_configured' },
    { key: 'database', label: 'Database', checkpoint: 'database_configured' },
    { key: 'admin-user', label: 'Admin Account', checkpoint: 'admin_created' },
  ];

  if (topology === 'manager_worker') {
    steps.push({
      key: 'worker-password',
      label: 'Worker Password',
      checkpoint: 'worker_password_set',
    });
    steps.push({
      key: 'blender-download',
      label: 'Blender',
      checkpoint: 'blender_predownloaded',
    });
  }

  steps.push(
    { key: 'verification', label: 'Verify', checkpoint: 'verified' },
    { key: 'done', label: 'Done', checkpoint: null },
  );

  return steps;
}
