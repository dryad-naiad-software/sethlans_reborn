// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

/**
 * Shapes of the setup-mode API (`manager/sethlans_manager/setup_mode/`:
 * `form.py`, `worker_form.py`, `submit.py`, `worker_outcome.py`). Setup
 * mode is not in the OpenAPI schema, so these are written by hand. Every
 * text in them is shown through Angular interpolation only.
 */

export type SetupMode = 'manager' | 'manager_worker' | 'worker';
export type ComputeMethod = 'cpu' | 'gpu' | 'cpu_gpu';
export type DatabaseEngine = 'sqlite' | 'postgresql' | 'mysql';
export type PostgresTlsMode = 'verify-full' | 'verify-ca' | 'require';
export type CardBrand = 'NVIDIA' | 'AMD' | 'Intel' | 'Apple';

/** `GET /api/health/` in setup mode (`mode: setup`) and in the running manager (`mode: normal`). */
export interface SetupHealth {
  mode: 'setup' | 'normal';
  boot_id: string;
  version?: string;
  last_failure?: string | null;
}

export interface SetupServer {
  host: string;
  port: number | null;
}

export interface SetupDataFolder {
  path: string;
  free_bytes: number | null;
  low_space: boolean;
}

export interface SetupDatabase {
  engine: DatabaseEngine;
  name: string;
  host: string;
  port: number | null;
  user: string;
  /** Always empty from the server; empty on submit keeps the stored one. */
  password: string;
  password_set: boolean;
  tls: boolean;
  tls_ca_file: string;
  tls_mode: PostgresTlsMode;
}

export interface SetupAdmin {
  accounts_exist: boolean;
  keep_existing: boolean;
  username: string;
  email: string;
  password: string;
  replace_password: boolean;
}

export interface SetupWorker {
  compute: ComputeMethod;
  /** Ticked card addresses. */
  cards: string[];
  combine: boolean;
  unreal_enabled: boolean;
  ui_password: string;
  ui_password_set: boolean;
  compute_env_override: boolean;
  unreal_enabled_env: boolean;
}

export interface SetupCard {
  address: string;
  key: string;
  pci: string | null;
  name: string;
  brand: CardBrand | null;
  vendor_id: string | number | null;
  device_id: string | number | null;
  revision_id: string | number | null;
  screen: boolean;
  primary_screen: boolean;
}

export interface DiscoveredManager {
  name: string;
  host: string;
  ip: string;
  port: number;
  manager_id: string;
  version: string;
  url: string | null;
}

/** The manager this worker is enrolled with (`worker_form._enrollment`); no token. */
export interface SetupEnrollment {
  manager_id: string | null;
  manager_name: string | null;
  host: string | null;
  port: number | null;
}

export interface SetupManagerChoice {
  url: string;
  name: string;
  enrollment_key: string;
  keep_enrollment: boolean;
}

/** `GET /api/setup/form/`; its answer parts (`SetupAnswers`) go to `POST /api/setup/submit/`. */
export interface SetupForm {
  mode: SetupMode;
  modes: SetupMode[];
  hostname: string;
  addresses: string[];
  bind_addresses: string[];
  server: SetupServer;
  data_folder: SetupDataFolder;
  database: SetupDatabase;
  admin: SetupAdmin;
  worker: SetupWorker;
  cards: SetupCard[];
  cards_os_probe_failed: boolean;
  managers: DiscoveredManager[];
  enrollment: SetupEnrollment | null;
  manager: SetupManagerChoice;
  leftovers: string[];
  last_failure: string | null;
  errors: SetupNotice[];
}

/** One problem found while building the form; `part` says what it is about. */
export interface SetupNotice {
  part: 'worker' | 'database' | 'settings';
  message: string;
}

/**
 * The answer parts of `POST /api/setup/submit/`: only these are sent,
 * never the form's lists, notices or other read-only parts.
 */
export interface SetupAnswers {
  mode: SetupMode;
  server: SetupServer;
  data_folder: { path: string };
  database: SetupDatabase;
  admin: SetupAdmin;
  worker: SetupWorker;
  manager: SetupManagerChoice;
}

/** The page's own "password again" fields (never sent). */
export interface PasswordConfirm {
  admin: string;
  worker: string;
}

/** Messages by dotted field key (`admin.username`) plus `non_field_errors`. */
export type FieldErrors = Record<string, string[]>;

/** Machine-readable reasons by dotted field key (`{"admin.username": ["account_exists"]}`). */
export type FieldCodes = Record<string, string[]>;

/** The `worker` part of the submit's 200 answer. */
export interface WorkerFinish {
  enrolled: boolean;
  kept_enrollment: boolean;
  manager_url: string | null;
  manager_id: string | null;
  manager_name: string | null;
  cert_fingerprint: string | null;
  status: string | null;
}

/** `POST /api/setup/submit/` 200. */
export interface FinishInfo {
  ok: boolean;
  mode: SetupMode;
  restarting: boolean;
  url: string | null;
  port_changed: boolean;
  first_admin: string | null;
  worker: WorkerFinish | null;
  warnings: string[];
  leftovers: string[];
}

/** Any non-2xx answer of the setup API, made uniform by `SetupApiService`. */
export interface SetupFailure {
  /** HTTP status; 0 when the server could not be reached. */
  status: number;
  code: string;
  message: string;
  fields: FieldErrors;
  /** The 400 `invalid` body's `codes`; empty otherwise. */
  codes: FieldCodes;
}
