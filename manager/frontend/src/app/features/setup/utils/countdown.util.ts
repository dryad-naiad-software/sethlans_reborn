// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Formats a whole number of seconds as "m:ss" (e.g. 297 -> "4:57").
 * Negative values and NaN clamp to "0:00".
 */
export function formatCountdown(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0:00';
  }
  const total = Math.floor(seconds);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/**
 * Computes whole seconds remaining until an absolute epoch-ms deadline.
 * Clamps to 0 when the deadline is in the past.
 */
export function secondsRemaining(
  deadlineEpochMs: number,
  nowEpochMs: number = Date.now(),
): number {
  const diffMs = deadlineEpochMs - nowEpochMs;
  if (diffMs <= 0) {
    return 0;
  }
  return Math.ceil(diffMs / 1000);
}
