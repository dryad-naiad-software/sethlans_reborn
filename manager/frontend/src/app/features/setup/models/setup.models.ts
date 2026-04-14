// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

export type Topology = 'manager' | 'manager_worker' | 'worker_only';

export interface SetupStatus {
  complete: boolean;
  topology: Topology | null;
  current_step: string | null;
  checkpoints: string[];
}

export interface TopologyRequest {
  topology: Topology;
}

export interface TopologyResponse {
  status: string;
}

export interface NetworkRequest {
  bind_host: string;
  bind_port: number;
  data_dir?: string;
}

export interface NetworkResponse {
  status: string;
  bind_host: string;
  bind_port: number;
}

export interface DatabaseRequest {
  engine: 'sqlite' | 'postgresql' | 'mysql' | 'custom';
  host?: string;
  port?: string;
  name?: string;
  user?: string;
  password?: string;
  engine_path?: string;
}

export interface DatabaseResponse {
  status: 'ok' | 'restart_required';
}

export interface AdminUserRequest {
  username: string;
  email: string;
  password: string;
  password_confirm: string;
}

export interface AdminUserResponse {
  status: string;
  username: string;
}

export interface WorkerPasswordRequest {
  password: string;
}

export interface WorkerPasswordResponse {
  status: string;
}

export interface DownloadStartResponse {
  status: 'started' | 'in_progress' | 'already_installed';
  task_id: string | null;
  version?: string;
}

export interface DownloadProgress {
  status: 'pending' | 'downloading' | 'extracting' | 'verifying' | 'complete' | 'failed';
  percent: number;
  error: string | null;
}

export interface DownloadCancelResponse {
  status: string;
}

export interface VerifyCheck {
  name: string;
  passed: boolean;
  error: string | null;
}

export interface VerifyResponse {
  checks: VerifyCheck[];
  all_passed: boolean;
  error?: string;
}

export interface SetupSummary {
  manager_url: string;
  admin_username: string;
  enrollment_key: string;
  cert_fingerprint: string;
  topology: string;
}
