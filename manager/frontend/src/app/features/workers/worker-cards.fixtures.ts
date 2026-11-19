// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/** Test data for the worker list and card panel specs. */

import { Worker } from '../../core/services/worker.service';
import {
  CardAlert, CardChoiceOutcome, CardChoiceRequest, CardEntry, CardStatus,
} from '../../core/services/worker-cards.model';

/** Card text that must stay text (C-25k). */
export const HOSTILE_TEXT = '"><img src=x onerror=alert(1)>';
export const HOSTILE_URL = 'javascript:alert(1)';

export function makeCard(overrides: Partial<CardEntry> = {}): CardEntry {
  return {
    key: 'pci:0000:01:00.0', state: 'ok', name: 'NVIDIA GeForce RTX 5090',
    vendor_id: 0x10de, device_id: 0x2b85, ticked: true, blender: true,
    backend: 'OPTIX', note: '', screen: null, primary_screen: null,
    ...overrides,
  };
}

export function makeAlert(overrides: Partial<CardAlert> = {}): CardAlert {
  return {
    id: 'a'.repeat(32), kind: 'card_lost', key: 'pci:0000:01:00.0',
    name: 'NVIDIA GeForce RTX 5090', detail: 'missing from the card list',
    raised_at: '2026-11-19T10:00:00+00:00',
    ...overrides,
  };
}

export function makeStatus(overrides: Partial<CardStatus> = {}): CardStatus {
  return {
    schema: 1, method: 'gpu', gpu_mode: 'split', env_override: false, revision: 3,
    manager_changes_allowed: true, rebuild_waiting: false, problem: '',
    cards: [makeCard()], alerts: [], notices: [], warnings: [],
    ...overrides,
  };
}

export function makeWorker(overrides: Partial<Worker> = {}): Worker {
  return {
    id: 1, hostname: 'render-01', ip_address: '10.0.0.5', status: 'IDLE',
    cpu_name: 'AMD Ryzen 9', gpu_name: 'Legacy GPU name', os: 'Linux',
    ui_url: 'https://10.0.0.5:7443/', last_heartbeat: '2026-11-19T10:00:00Z',
    is_active: true, has_token: true,
    card_status: makeStatus(), card_alert_dismissals: [],
    card_choice_request: null, card_choice_outcome: null,
    ...overrides,
  };
}

export function makeRequest(overrides: Partial<CardChoiceRequest> = {}): CardChoiceRequest {
  return {
    id: 'd'.repeat(32), method: 'gpu', gpu_mode: 'split',
    cards: [{ key: 'pci:0000:01:00.0', vendor_id: 0x10de, device_id: 0x2b85 }],
    shown: ['pci:0000:01:00.0'], base_revision: 3,
    requested_by: 'user id=5', requested_at: '2026-11-19T10:00:00+00:00',
    ...overrides,
  };
}

export function makeOutcome(overrides: Partial<CardChoiceOutcome> = {}): CardChoiceOutcome {
  return {
    id: 'e'.repeat(32), result: 'applied', code: 'applied', reason: '', revision: 4,
    finished_at: '2026-11-19T10:05:00+00:00',
    requested_by: 'user id=5', requested_at: '2026-11-19T10:00:00+00:00',
    ...overrides,
  };
}
