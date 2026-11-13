import { requestJson } from './client';

export interface SystemStatus {
  initialized: boolean;
  auth_enabled?: boolean;
  default_llm_server?: string;
  message?: string;
  schedule_state?: 'STOPPED' | 'BLOCKED' | 'RUNNING' | 'stopped' | 'blocked' | 'running';
  not_running_reason?: string;
  language?: string;
  development_mode?: boolean;
  version?: string;
  auto_check_update?: boolean;
  /** 后端是否打开了前端日志开关（TOGOSPACE_FRONTEND_DEBUG），决定是否经 ws 转发日志。 */
  frontend_debug?: boolean;
}

export async function checkSystemStatus(): Promise<{ initialized: boolean }> {
  return await requestJson<{ initialized: boolean }>('/system/status.json');
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return requestJson<SystemStatus>('/system/status.json');
}

export async function resumeSchedule(): Promise<{
  status: string;
  schedule_state: 'STOPPED' | 'BLOCKED' | 'RUNNING' | 'stopped' | 'blocked' | 'running';
  not_running_reason?: string;
}> {
  return requestJson('/system/schedule/resume.json', {
    method: 'POST',
  });
}

export async function backupDatabase(): Promise<{
  status: string;
  backup_path: string;
  backup_file_name: string;
}> {
  return requestJson('/system/database/backup.json', {
    method: 'POST',
  });
}

export interface UpdateCheckResult {
  has_update: boolean;
  current_version: string;
  latest_version: string;
  release_url: string;
  release_notes: string;
}

export async function checkUpdate(force = false): Promise<UpdateCheckResult> {
  const qs = force ? '?force=true' : '';
  return requestJson<UpdateCheckResult>(`/system/check_update.json${qs}`);
}

export async function updateSystemConfig(payload: { auto_check_update?: boolean }): Promise<{ auto_check_update: boolean }> {
  return requestJson('/system/update_config.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
