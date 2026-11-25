export type MemberCategory = 'STUDENT' | 'LECTURER';

export interface LecturerOption {
  member_id: string;
  username: string;
  full_name: string;
  email: string;
  member_code: string;
  department_class: string;
}

export interface SupervisedStudentSummary {
  member_id: string;
  username: string;
  full_name: string;
  member_code: string;
  department_class: string;
  email: string;
  web_account_status: string;
  ssh_access_state: string;
  ssh_expires_at: string | null;
  is_expiring_soon: boolean;
  pending_request_id: string | null;
  latest_project: string | null;
  assigned_server: string | null;
  cpu_limit_cores: number;
  ram_limit_gb: number;
  storage_quota_gb: number;
  gpu_permissions: string[];
  direct_manager_id?: string | null;
}

export interface SupervisorGroup {
  supervisor_id: string;
  supervisor_name: string;
  supervisor_username: string | null;
  department_class: string;
  email: string;
  total_students: number;
  pending_requests: number;
  active_ssh_count: number;
  expiring_soon_count: number;
  projects: string[];
  hosts: string[];
  total_storage_quota_gb: number;
  total_gpu_count: number;
  students: SupervisedStudentSummary[];
}

export interface SshRequestRecommendation {
  recommended_host_id: string | null;
  recommended_pool_id: string | null;
  recommended_filesystem_uuid: string | null;
  recommended_mount_point: string | null;
  cpu_cores: number;
  ram_gb: number;
  storage_quota_gb: number;
  gpu_mode: string;
  gpu_uuid: string | null;
  gpu_vram_gb?: number | null;
  duration_days: number | null;
  manual_review_required: boolean;
  rationale?: Record<string, unknown>;
}

export interface ServerAccessRequestItem {
  id: string;
  request_type: string;
  member_id: string;
  username: string;
  full_name: string;
  email: string;
  member_code: string;
  department_class: string;
  member_category: MemberCategory;
  primary_supervisor_id: string | null;
  supervisor_name: string | null;
  direct_manager_id: string | null;
  project_title: string;
  registration_reason: string;
  ssh_purpose: string;
  expected_tasks: string;
  requested_duration_days: number | null;
  gpu_requested: boolean;
  gpu_justification: string;
  policy_version: string;
  status: string;
  version: number;
  submitted_at: string | null;
  review_expires_at: string | null;
  remaining_review_seconds: number;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string;
  parent_request_id: string | null;
  recommendation: SshRequestRecommendation | null;
  latest_approval?: {
    id: string;
    decision: string;
    approver_username: string;
    reason: string;
    approved_config: Record<string, unknown>;
    config_hash: string;
    decided_at: string | null;
  } | null;
  provisioning?: {
    id: string;
    status: string;
    steps: Array<Record<string, unknown>>;
    verification: Record<string, unknown>;
    last_error?: string | null;
    completed_at?: string | null;
  } | null;
}

export interface WebPortalAccountItem {
  member_id: string;
  username: string;
  full_name: string;
  email: string;
  member_code: string;
  member_category: MemberCategory;
  department_class: string;
  primary_supervisor_id: string | null;
  supervisor_name: string | null;
  web_account_status: string;
  ssh_access_state: string;
  ssh_expires_at: string | null;
  must_change_password: boolean;
  has_web_credential: boolean;
  created_by: string;
  created_at: string | null;
  is_ssh_linked: boolean;
  linked_ssh_member_id: string | null;
  linked_ssh_username: string | null;
  linked_ssh_server: string | null;
  linked_ssh_host: string | null;
  linked_ssh_port: number | null;
  latest_request: {
    id: string;
    status: string;
    project_title: string;
    gpu_requested: boolean;
    requested_duration_days: number | null;
    submitted_at: string | null;
    decided_at: string | null;
  } | null;
}

export function formatRemainingSla(seconds: number): string {
  if (seconds <= 0) return 'Hết hạn (00:00)';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${String(m).padStart(2, '0')}m`;
}
