// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

import { SetupErrorCode, SetupErrorEnvelope } from './error-envelope';

/**
 * Compile-check only: asserts every backend error code in the spec
 * (workers/utils/errors.py::ERROR_CODES) is representable as SetupErrorCode.
 * Failing to compile == drift against the backend enum.
 */
describe('error-envelope types', () => {
  it('covers every backend error code as a SetupErrorCode', () => {
    const allCodes: SetupErrorCode[] = [
      'invalid_token',
      'rate_limited',
      'setup_complete',
      'setup_in_progress',
      'precondition_unmet',
      'setup_session_conflict',
      'invalid_input',
      'internal_error',
    ];
    expect(allCodes.length).toBe(8);
    // Spot-check: each assignment must type-check.
    const a: SetupErrorCode = 'invalid_token';
    const b: SetupErrorCode = 'rate_limited';
    const c: SetupErrorCode = 'setup_session_conflict';
    expect([a, b, c]).toEqual(
      ['invalid_token', 'rate_limited', 'setup_session_conflict'],
    );
  });

  it('SetupErrorEnvelope has the backend response shape', () => {
    const envelope: SetupErrorEnvelope = {
      error: {
        code: 'invalid_token',
        message: 'bad',
        details: {},
      },
    };
    expect(envelope.error.code).toBe('invalid_token');
    expect(envelope.error.message).toBe('bad');
    expect(envelope.error.details).toEqual({});
  });
});
