// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { Injectable } from '@angular/core';
import { Topology, SetupStatus } from '../models/setup.models';
import { StepConfig, getStepsForTopology } from '../setup-step-config';

// Legacy key from the X-Setup-Token era. Cleared once at service construction
// so stale tokens from prior frontend versions cannot persist.
const LEGACY_SETUP_TOKEN_STORAGE_KEY = 'sethlans.setupToken';

@Injectable({ providedIn: 'root' })
export class SetupStateService {
  topology: Topology | null = null;
  checkpoints: string[] = [];
  visibleSteps: StepConfig[] = getStepsForTopology(null);

  private adminPassword: string | null = null;

  constructor() {
    try {
      if (typeof sessionStorage !== 'undefined') {
        sessionStorage.removeItem(LEGACY_SETUP_TOKEN_STORAGE_KEY);
      }
    } catch {
      // Ignore — sessionStorage unavailable.
    }
  }

  setTopology(topology: Topology): void {
    this.topology = topology;
    this.visibleSteps = getStepsForTopology(topology);
  }

  setAdminPassword(password: string): void {
    this.adminPassword = password;
  }

  getAdminPassword(): string | null {
    return this.adminPassword;
  }

  clearSensitiveData(): void {
    this.adminPassword = null;
  }

  markCheckpoint(name: string): void {
    if (!this.checkpoints.includes(name)) {
      this.checkpoints.push(name);
    }
  }

  /**
   * Restores state from server status and returns the step index to resume at.
   */
  resumeFromStatus(status: SetupStatus): number {
    if (status.topology) {
      this.topology = status.topology;
      this.visibleSteps = getStepsForTopology(status.topology);
    }
    this.checkpoints = [...status.checkpoints];

    // Find the first step whose checkpoint is not yet completed
    for (let i = 0; i < this.visibleSteps.length; i++) {
      const step = this.visibleSteps[i];
      if (step.checkpoint && !this.checkpoints.includes(step.checkpoint)) {
        return i;
      }
    }

    // All checkpoints complete — go to the last step (done)
    return this.visibleSteps.length - 1;
  }
}
