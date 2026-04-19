// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

export type SetupErrorCode =
  | 'invalid_token'
  | 'rate_limited'
  | 'setup_complete'
  | 'setup_in_progress'
  | 'precondition_unmet'
  | 'invalid_input'
  | 'internal_error'
  | 'setup_session_conflict';

export interface SetupErrorEnvelope {
  error: {
    code: SetupErrorCode;
    message: string;
    details: Record<string, unknown>;
  };
}
