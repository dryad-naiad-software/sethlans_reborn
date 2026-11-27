// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { DiscoveredManager, FinishInfo, SetupCard, SetupForm } from './setup.types';

/**
 * Test fixtures for the setup page, in the shape of `GET /api/setup/form/`
 * (`manager/sethlans_manager/setup_mode/form.py`, `worker_form.py`) and the
 * submit's 200 (`submit.py`, `worker_outcome.py`). A fresh form: nothing
 * stored, no card ticked.
 */

export function setupForm(): SetupForm {
  return {
    mode: 'manager_worker',
    modes: ['manager', 'manager_worker', 'worker'],
    hostname: 'render-01',
    addresses: ['192.168.1.20'],
    bind_addresses: ['0.0.0.0', '127.0.0.1', '192.168.1.20'],
    server: { host: '0.0.0.0', port: 8080 },
    data_folder: { path: '/srv/sethlans', free_bytes: 50_000_000_000, low_space: false },
    database: {
      engine: 'sqlite', name: '', host: '', port: null, user: '', password: '',
      password_set: false, tls: false, tls_ca_file: '', tls_mode: 'verify-full',
    },
    admin: {
      accounts_exist: false, keep_existing: false, username: '', email: '', password: '',
      replace_password: false,
    },
    worker: {
      compute: 'cpu', cards: [], combine: false, unreal_enabled: false, ui_password: '',
      ui_password_set: false, compute_env_override: false, unreal_enabled_env: false,
    },
    cards: [],
    cards_os_probe_failed: false,
    managers: [],
    enrollment: null,
    manager: { url: '', name: '', enrollment_key: '', keep_enrollment: false },
    leftovers: [],
    last_failure: null,
    errors: [],
  };
}

export function setupCard(overrides: Partial<SetupCard> = {}): SetupCard {
  return {
    address: 'gpu0', key: '10de:2684:a1', pci: '0000:01:00.0', name: 'GeForce RTX 4090',
    brand: 'NVIDIA', vendor_id: '10de', device_id: '2684', revision_id: 'a1',
    screen: false, primary_screen: false,
    ...overrides,
  };
}

export function discoveredManager(overrides: Partial<DiscoveredManager> = {}): DiscoveredManager {
  return {
    name: 'Studio manager', host: 'studio.local', ip: '192.168.1.10', port: 8080,
    manager_id: 'a1b2c3d4-0000-4000-8000-000000000001', version: '0.1.0',
    url: 'https://studio.local:8080',
    ...overrides,
  };
}

export function finishInfo(overrides: Partial<FinishInfo> = {}): FinishInfo {
  return {
    ok: true, mode: 'manager_worker', restarting: true, url: null, port_changed: false,
    first_admin: 'admin', worker: null, warnings: [], leftovers: [],
    ...overrides,
  };
}
