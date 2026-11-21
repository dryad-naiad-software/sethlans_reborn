// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Pure helpers for the worker card report (worker card selection FR-CF-8).
 * They only pick and label values; every text from the worker reaches the
 * page through Angular interpolation, never as HTML, a URL or a style.
 */

import {
  CardAlert, CardAlertKind, CardBackend, CardEntry, CardNotice, CardState, CardStatus,
} from '../../core/services/worker-cards.model';

export type CardTone = 'bad' | 'amber' | 'good' | 'plain';

const STATE_LABELS: Record<CardState, string> = {
  ok: 'OK',
  unticked: 'Not ticked',
  lost: 'Missing',
  changed: 'A different card is in this slot',
  held: 'Held until its alert is dismissed',
  blender_lost: 'Blender no longer offers it (Unreal may still use it)',
  unknown: 'Cannot be checked right now',
};

const STATE_TONES: Record<CardState, CardTone> = {
  ok: 'good', unticked: 'plain', lost: 'bad', changed: 'bad', held: 'bad',
  blender_lost: 'amber', unknown: 'amber',
};

const ALERT_LABELS: Record<CardAlertKind, string> = {
  card_lost: 'Card missing',
  card_changed: 'Card changed',
  card_lost_blender: 'Blender no longer offers the card',
  adapter_mismatch: 'Unreal used another card',
};

const NOTICE_PREFIX: Record<CardNotice['kind'], string> = {
  card_new: 'New card found: ',
  card_gone: 'Card no longer present: ',
};

/** CUDA and OptiX are one family (FR-CC-4). */
const FAMILIES: Record<CardBackend, string> = {
  CUDA: 'CUDA', OPTIX: 'CUDA', HIP: 'HIP', ONEAPI: 'ONEAPI', METAL: 'METAL',
};

/** A value of a fixed table, never an inherited member such as `toString`. */
function lookup<V>(table: Record<string, V>, key: unknown, fallback: V): V {
  return typeof key === 'string' && Object.hasOwn(table, key) ? table[key] : fallback;
}

const KEY_ADDRESS = /^pci:([0-9a-f]{4,8}):([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])$/;

/** True only for a report with `schema === 1`; `null`, `undefined` and `{}` are absent. */
export function hasCardStatus(status: CardStatus | null | undefined): status is CardStatus {
  return !!status && status.schema === 1;
}

export function cardsOf(status: CardStatus | null | undefined): CardEntry[] {
  return hasCardStatus(status) && Array.isArray(status.cards) ? status.cards : [];
}

export function alertsOf(status: CardStatus | null | undefined): CardAlert[] {
  return hasCardStatus(status) && Array.isArray(status.alerts) ? status.alerts : [];
}

export function alertCount(status: CardStatus | null | undefined): number {
  return alertsOf(status).length;
}

/** `bus:device.function` of a `pci:` key, or `null` (for example `apple-gpu`). */
export function shortAddress(key: string): string | null {
  const match = KEY_ADDRESS.exec(key);
  return match ? `${match[2]}:${match[3]}.${match[4]}` : null;
}

/** `PCI 01:00.0`, or an empty string for a card without a PCI address. */
export function addressLabel(key: string): string {
  const address = shortAddress(key);
  return address ? `PCI ${address}` : '';
}

/** The card's name, else its address, else a generic name. */
export function cardName(card: { key: string; name?: string }): string {
  return card.name || addressLabel(card.key) || 'Graphics card';
}

export function stateLabel(state: CardState): string {
  return lookup(STATE_LABELS, state, 'Unknown state');
}

export function stateTone(state: CardState): CardTone {
  return lookup<CardTone>(STATE_TONES, state, 'plain');
}

/** FR-CS-4 screen marks: `false` shows none; `null`, absent or any other value is unknown. */
export function screenLabels(card: Pick<CardEntry, 'screen' | 'primary_screen'>): string[] {
  if (card.screen === false) return [];
  if (card.screen !== true) return ['Screen: unknown'];
  return card.primary_screen === true ? ['Drives a screen', 'Main screen'] : ['Drives a screen'];
}

export function alertLabel(kind: CardAlertKind): string {
  return lookup(ALERT_LABELS, kind, 'Card alert');
}

export function noticeText(notice: CardNotice): string {
  return lookup(NOTICE_PREFIX, notice.kind, 'Card notice: ') + cardName(notice);
}

/**
 * The GPU column: the ticked cards' names when the report has cards, else
 * the worker's `gpu_name` as before.
 */
export function gpuColumnText(status: CardStatus | null | undefined, gpuName: string): string {
  const cards = cardsOf(status);
  if (cards.length === 0) return gpuName;
  const ticked = cards.filter(c => c.ticked === true).map(c => cardName(c));
  return ticked.length > 0 ? ticked.join(', ') : 'No card ticked';
}

/** FR-CC-4: at least two non-null backends, all of one family. */
export function oneFamily(cards: Pick<CardEntry, 'backend'>[]): boolean {
  const families = cards
    .map(c => c.backend)
    .filter((b): b is CardBackend => b !== null && b !== undefined)
    .map(b => lookup<string>(FAMILIES, b, b));
  return families.length >= 2 && families.every(f => f === families[0]);
}

/** D7: brand names of the known PCI vendor ids. */
const VENDOR_BRANDS: Record<number, string> = { 0x10de: 'NVIDIA', 0x1002: 'AMD', 0x8086: 'Intel' };

/** D7: the brand of a Blender backend family, used only when the card has no vendor id. */
const BACKEND_BRANDS: Record<CardBackend, string> = {
  CUDA: 'NVIDIA', OPTIX: 'NVIDIA', HIP: 'AMD', ONEAPI: 'Intel', METAL: 'Apple',
};

/**
 * The card's brand per D7: its PCI vendor id (`PCI <vvvv>` for another
 * vendor), else its Blender backend family, else `null` (unknown).
 */
export function cardBrand(card: Pick<CardEntry, 'vendor_id' | 'backend'>): string | null {
  const vendor = card.vendor_id;
  if (typeof vendor === 'number' && Number.isInteger(vendor) && vendor >= 0 && vendor <= 0xffff) {
    return Object.hasOwn(VENDOR_BRANDS, vendor)
      ? VENDOR_BRANDS[vendor] : `PCI ${vendor.toString(16).padStart(4, '0')}`;
  }
  return lookup<string | null>(BACKEND_BRANDS, card.backend, null);
}

/**
 * FR-PC-BR-5, worded as on the worker pages: the note of a card of brand
 * `own` beside ticked cards of `tickedBrands`, or `null` when they match.
 * Any unknown brand gives "other cards"; else the first differing ticked brand is named.
 */
export function brandNote(own: string | null, tickedBrands: readonly (string | null)[]): string | null {
  if (tickedBrands.length === 0) return null;
  if (own === null || tickedBrands.includes(null)) return 'Not supported together with other cards.';
  const other = tickedBrands.find(b => b !== own);
  return other === undefined ? null : `Not supported together with ${other} cards.`;
}

/** The time text for `DatePipe`, or `null` when absent or not a date (the pipe would throw). */
export function validTime(value: string | null | undefined): string | null {
  return typeof value === 'string' && value !== '' && !Number.isNaN(Date.parse(value))
    ? value : null;
}

/** The error text of a failed card action: its `detail`, else the HTTP status (R6). */
export function actionErrorText(status: number, body: unknown): string {
  if (body !== null && typeof body === 'object') {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === 'string' && detail.trim() !== '') return detail;
  }
  return `The request failed (HTTP ${status}).`;
}
