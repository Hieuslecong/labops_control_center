export type QualityStatus =
  | 'FRESH'
  | 'STALE'
  | 'OFFLINE'
  | 'NOT_CONFIGURED'
  | 'UNSUPPORTED'
  | 'NO_DATA'
  | 'ERROR'
  | 'UNKNOWN'
  | 'PARTIAL';

export type WorkloadKind =
  | 'TRAINING'
  | 'INFERENCE'
  | 'NOTEBOOK'
  | 'GENERAL_COMPUTE'
  | 'MONITORING'
  | 'SYSTEM'
  | 'OTHER'
  | 'UNKNOWN';

export type KindConfidence = 'VERIFIED' | 'HEURISTIC' | 'UNKNOWN';
export type CpuBasis = 'PER_CORE_PERCENT' | 'HOST_CAPACITY_PERCENT' | 'UNKNOWN';
export type MemoryMethod =
  | 'PROCESS_RSS'
  | 'TREE_RSS_SUM'
  | 'TREE_PSS_SUM'
  | 'WORKSPACE_CGROUP_CURRENT'
  | 'UNKNOWN';
export type VramAttribution = 'PER_DEVICE_VERIFIED' | 'LEGACY_AGGREGATE' | 'UNKNOWN';
export type WorkloadState =
  | 'OBSERVED'
  | 'STALE'
  | 'MISSING_UNCONFIRMED'
  | 'EXIT_CONFIRMED'
  | 'UNKNOWN';
export type ReconciliationStatus = 'CONSISTENT' | 'MISMATCH' | 'NOT_COMPARABLE' | 'UNKNOWN';
export type CapabilityStatus = 'SUPPORTED' | 'PARTIAL' | 'UNSUPPORTED' | 'NO_DATA' | 'DEFERRED';
export type ActionKind = 'SIGTERM' | 'SIGKILL';
export type ActionResultStatus =
  | 'TERMINATED'
  | 'KILLED'
  | 'TIMEOUT_STILL_RUNNING'
  | 'FAILED_STILL_RUNNING'
  | 'PARTIAL_FAILURE'
  | 'UNKNOWN'
  | 'FAILED'
  | 'DENIED';
export type ActionVerification =
  | 'VERIFIED'
  | 'PARTIAL_FAILURE'
  | 'STILL_RUNNING'
  | 'FAILED'
  | 'UNKNOWN';

export interface SnapshotScope {
  node_id: string | null;
  gpu_uuid: string | null;
  member_id: string | null;
  group_id: string | null;
  mode: 'workloads';
}

export interface ScopeBase {
  node_id: string | null;
  gpu_uuid: string | null;
  member_id: string | null;
  group_id: string | null;
}

export interface SnapshotQuality {
  status: QualityStatus;
  expected_nodes: number | null;
  fresh_nodes: number;
  fresh_workload_nodes: number;
  expected_gpus: number | null;
  fresh_gpus: number;
  last_success_at: string | null;
}

export interface SnapshotNodeItem {
  id: string;
  quality: QualityStatus;
  workload_quality: QualityStatus;
  observed_at: string | null;
  last_success_at: string | null;
}

export interface SnapshotGpuItem {
  gpu_uuid: string;
  node_id: string;
  quality: QualityStatus;
  observed_at: string | null;
  vram_used_bytes: number | null;
  vram_capacity_bytes: number | null;
  utilization_percent: number | null;
}

export interface GpuCoveragePair {
  fresh: number;
  expected: number | null;
}

export interface SnapshotKpis {
  observed_workloads: number | null;
  last_known_workloads: number | null;
  gpu_associated_workloads: number | null;
  gpu_association_unknown: number | null;
  observed_members: number | null;
  unmapped_member_workloads: number | null;
  device_vram_used_bytes: number | null;
  device_vram_capacity_bytes: number | null;
  attention_count: number | null;
  gpu_coverage: GpuCoveragePair;
}

export interface WorkloadComposition {
  gpu_non_monitoring: number;
  non_gpu_compute: number;
  monitoring: number;
  other_unknown: number;
}

export interface WorkloadCpuMetric {
  value_percent: number | null;
  basis: CpuBasis;
}

export interface WorkloadMemoryMetric {
  value_bytes: number | null;
  method: MemoryMethod;
  unique_memory: boolean | null;
}

export interface WorkloadVramMetric {
  value_bytes: number | null;
  attribution: VramAttribution;
}

export interface WorkloadGpuAllocation {
  gpu_uuid: string;
  value_bytes: number;
  quality: QualityStatus;
}

export interface SnapshotWorkloadItem {
  id: string;
  ref: string | null;
  node_id: string;
  boot_id: string | null;
  host_pid: number;
  start_ticks: number | null;
  container_id: string | null;
  uid: number | null;
  member_id: string | null;
  username: string | null;
  group_id: string | null;
  workspace: string | null;
  display_name: string;
  workload_kind: WorkloadKind;
  kind_confidence: KindConfidence;
  kind_evidence: string[];
  gpu_associated: boolean | null;
  cpu: WorkloadCpuMetric;
  memory: WorkloadMemoryMetric;
  vram: WorkloadVramMetric;
  gpu_allocations: WorkloadGpuAllocation[] | null;
  elapsed_seconds: number | null;
  started_at: string | null;
  state: WorkloadState;
  quality: QualityStatus;
  observed_at: string | null;
  is_protected: boolean;
  actions_allowed: boolean;
  action_block_reason: string | null;
}

export interface DeviceVramMeta {
  scope: 'DEVICE_WIDE';
  member_filter_applied: false;
  reconciliation: ReconciliationStatus;
}

export interface SnapshotWarning {
  code: string;
  message: string;
}

export interface WorkloadSnapshotEnvelope {
  schema_version: 1;
  snapshot_id: string;
  observed_at: string | null;
  received_at: string;
  scope: SnapshotScope;
  quality: SnapshotQuality;
  nodes: SnapshotNodeItem[];
  gpus: SnapshotGpuItem[];
  kpis: SnapshotKpis;
  composition: WorkloadComposition | null;
  workloads: SnapshotWorkloadItem[];
  device_vram: DeviceVramMeta;
  warnings: SnapshotWarning[];
}

export interface AnalyticsPoint {
  ts: string;
  value: number | null;
  quality: QualityStatus;
}

export interface AnalyticsSeries {
  metric: string;
  entity_id: string;
  unit: string;
  aggregation: string;
  coverage_ratio: number | null;
  points: AnalyticsPoint[];
}

export interface CapabilityEntry {
  status: CapabilityStatus;
  reason: string;
}

export interface WorkloadAnalyticsEnvelope {
  schema_version: 1;
  scope: ScopeBase;
  from: string;
  to: string;
  bucket_seconds: number;
  coverage_ratio: number | null;
  series: AnalyticsSeries[];
  capabilities: Record<string, CapabilityEntry>;
}

export interface HistoryWorkloadItem {
  id: string;
  node_id: string;
  member_id: string | null;
  group_id: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  observed_seconds: number | null;
  state: WorkloadState;
  exit_code: number | null;
  exit_reason: string | null;
  evidence: string;
  actionable: false;
}

export interface WorkloadHistoryEnvelope {
  schema_version: 1;
  scope: ScopeBase;
  from: string;
  to: string;
  items: HistoryWorkloadItem[];
  next_cursor: string | null;
  capability: CapabilityStatus;
}

export interface WorkloadEventItem {
  id: string;
  occurred_at: string;
  event_type: string;
  resource_id: string;
  actor_id: string | null;
  result: string;
  message: string;
  evidence: string;
  correlation_id: string | null;
}

export interface WorkloadEventsEnvelope {
  schema_version: 1;
  scope: ScopeBase;
  from: string;
  to: string;
  items: WorkloadEventItem[];
  next_cursor: string | null;
  capability: CapabilityStatus;
}

export interface ProcessActionResultEnvelope {
  schema_version: 1;
  action_id: string;
  action: ActionKind;
  status: ActionResultStatus;
  verification: ActionVerification;
  pids_remaining: number[];
  skipped_descendants: string[];
  message: string;
  correlation_id: string;
  executed_at: string | null;
}

export interface V31ProcessActionRequest {
  action: ActionKind;
  reason: string;
  operation_key: string;
  terminate_tree?: boolean;
  timeout_seconds?: number;
  expected_boot_id?: string | null;
  expected_start_ticks?: number | null;
  expected_uid?: number | null;
  expected_container_id?: string | null;
  force_confirmed?: boolean;
  typed_confirmation?: string | null;
}

export interface WorkloadDetailResponse {
  schema_version: 1;
  workload: SnapshotWorkloadItem;
  node_name: string;
  member_full_name: string | null;
  gpu_labels: Record<string, string>;
  cmdline_sanitized: string | null;
  children_count: number;
  pss_bytes: number | null;
  pss_status: 'SUPPORTED' | 'UNSUPPORTED' | 'PERMISSION_DENIED';
  group_attribution_mode: 'OBSERVED_AT' | 'CURRENT_MEMBER_PROFILE' | 'UNVERIFIED';
  metric_definitions: Record<string, string>;
  actions_enabled_by_flag: boolean;
}

export type WorkloadTabId = 'overview' | 'live' | 'analytics' | 'history';
export type HistoryRangePreset = '1h' | '6h' | '24h' | '7d' | '30d';

export interface WorkloadScopeFilters {
  nodeId: string;
  gpuUuid: string;
  memberId: string;
  groupId: string;
  range: HistoryRangePreset;
}
