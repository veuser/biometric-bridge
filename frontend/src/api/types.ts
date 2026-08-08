export type PunchRole = 'CHECK_IN' | 'CHECK_OUT' | 'FROM_DEVICE'

export interface CurrentUser {
  username: string
  role: 'ADMIN' | 'VIEWER'
}

export interface Device {
  id: number
  name: string
  ip_address: string
  port: number
  location: string | null
  serial_number: string | null
  device_type: string
  role: PunchRole
  timeout_seconds: number
  force_udp: boolean
  timezone: string | null
  enabled: boolean
  comm_key_set: boolean
  created_at: string
  updated_at: string
}

export interface DeviceInfo {
  serial_number: string | null
  firmware_version: string | null
  platform: string | null
  device_name: string | null
  user_count: number | null
  record_count: number | null
  device_time: string | null
}

export interface DeviceTestResult {
  reachable: boolean
  message: string
  info: DeviceInfo | null
  clock_offset_seconds: number | null
  clock_warning: string | null
}

export interface HrmsConfig {
  base_url: string
  tenant_identifier: string | null
  api_key_masked: string
  api_key_set: boolean
  profile: Record<string, unknown>
  connect_timeout_seconds: number
  request_timeout_seconds: number
  enabled: boolean
  profile_configured: boolean
}

export interface SyncOutcome {
  run_id: number
  device_id: number
  status: string
  buffer_records: number
  window_records: number
  records_sent: number
  records_success: number
  records_failed: number
  records_invalid: number
  records_suppressed: number
  watermark_advanced_to: string | null
  error_message: string | null
}

export interface DeviceSyncStatus {
  device_id: number
  device_name: string
  enabled: boolean
  last_successful_push_at: string | null
  last_sync_started_at: string | null
  last_sync_completed_at: string | null
  last_status: string | null
  last_error: string | null
  clock_offset_seconds: number | null
  clock_warning: string | null
  is_running: boolean
}

export interface SyncStatusResponse {
  auto_sync_enabled: boolean
  sync_interval_minutes: number
  devices: DeviceSyncStatus[]
}

export interface SyncRun {
  id: number
  device_id: number
  device_name: string | null
  trigger: string
  started_at: string
  completed_at: string | null
  from_time: string | null
  to_time: string | null
  buffer_records: number
  window_records: number
  records_sent: number
  records_success: number
  records_failed: number
  records_invalid: number
  records_suppressed: number
  device_read_ms: number | null
  hrms_ms: number | null
  duration_ms: number | null
  status: string
  error_message: string | null
}

export interface SyncHistoryPage {
  items: SyncRun[]
  total: number
  page: number
  page_size: number
}

export interface SyncSettings {
  auto_sync_enabled: boolean
  sync_interval_minutes: number
  sync_overlap_seconds: number
  initial_lookback_hours: number
  timezone: string
  clock_drift_warning_seconds: number
  max_clock_offset_delta_seconds: number
  manual_max_span_days: number
  stale_lock_minutes: number
  sync_run_retention_days: number
  interval_warning: string | null
}

export interface Dashboard {
  devices: {
    total: number
    enabled: number
    disabled: number
    with_clock_warning: number
    running: number
  }
  hrms: {
    configured: boolean
    enabled: boolean
    profile_configured: boolean
    api_key_set: boolean
    base_url: string | null
  }
  scheduler: {
    running: boolean
    auto_sync_enabled: boolean
    interval_minutes: number
    next_run_at: string | null
  }
  activity: {
    runs: number
    records_read: number
    records_pushed: number
    records_failed: number
    failed_runs: number
  }
  last_successful_sync_at: string | null
  last_failed_sync_at: string | null
  last_error: string | null
  warnings: string[]
}

export interface User {
  id: number
  username: string
  role: string
  is_active: boolean
  last_login_at: string | null
  locked_until: string | null
}

export interface LogEntry {
  timestamp: string | null
  level: string | null
  logger: string | null
  message: string
  extra: Record<string, unknown>
}

export interface LogPage {
  entries: LogEntry[]
  file: string
  truncated: boolean
}
