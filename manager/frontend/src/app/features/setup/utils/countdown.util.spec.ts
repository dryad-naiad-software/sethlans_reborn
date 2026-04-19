// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { formatCountdown, secondsRemaining } from './countdown.util';

describe('formatCountdown', () => {
  it('formats 0 as "0:00"', () => {
    expect(formatCountdown(0)).toBe('0:00');
  });

  it('formats 5 seconds as "0:05"', () => {
    expect(formatCountdown(5)).toBe('0:05');
  });

  it('formats 65 seconds as "1:05"', () => {
    expect(formatCountdown(65)).toBe('1:05');
  });

  it('formats 297 seconds as "4:57"', () => {
    expect(formatCountdown(297)).toBe('4:57');
  });

  it('formats 300 seconds as "5:00"', () => {
    expect(formatCountdown(300)).toBe('5:00');
  });

  it('clamps negative to "0:00"', () => {
    expect(formatCountdown(-10)).toBe('0:00');
  });

  it('clamps NaN to "0:00"', () => {
    expect(formatCountdown(NaN)).toBe('0:00');
  });

  it('floors fractional seconds', () => {
    expect(formatCountdown(59.9)).toBe('0:59');
  });
});

describe('secondsRemaining', () => {
  it('returns remaining whole seconds', () => {
    expect(secondsRemaining(10_000, 5_000)).toBe(5);
  });

  it('ceils partial seconds up', () => {
    expect(secondsRemaining(10_500, 10_000)).toBe(1);
  });

  it('clamps past deadline to 0', () => {
    expect(secondsRemaining(1_000, 5_000)).toBe(0);
  });

  it('returns 0 exactly at deadline', () => {
    expect(secondsRemaining(5_000, 5_000)).toBe(0);
  });
});
