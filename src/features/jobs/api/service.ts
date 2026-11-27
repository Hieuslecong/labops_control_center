import type {
  HistoryRangePreset,
  ProcessActionResultEnvelope,
  ScopeBase,
  SnapshotWorkloadItem,
  V31ProcessActionRequest,
  WorkloadAnalyticsEnvelope,
  WorkloadDetailResponse,
  WorkloadEventsEnvelope,
  WorkloadHistoryEnvelope,
  WorkloadSnapshotEnvelope
} from './types';

const GIB = 1024 ** 3;

const ENTITY_PALETTE = [
  'var(--chart-1, #2563b8)',
  'var(--chart-2, #178e83)',
  'var(--chart-3, #b77919)',
  'var(--chart-4, #6366f1)',
  'var(--chart-5, #0ea5e9)'
];

export function stableEntityColor(entityId: string | null | undefined): string {
  if (!entityId) return '#64748b';
  let hash = 2166136261;
  for (let i = 0; i < entityId.length; i += 1) {
    hash ^= entityId.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  const idx = Math.abs(hash) % ENTITY_PALETTE.length;
  return ENTITY_PALETTE[idx] ?? '#2563b8';
}

export function formatBytesGiB(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return '—';
  return `${(bytes / GIB).toFixed(1)} GiB`;
}

export function formatDurationSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const tot = Math.max(0, Math.floor(seconds));
  const d = Math.floor(tot / 86400);
  const h = Math.floor((tot % 86400) / 3600);
  const m = Math.floor((tot % 3600) / 60);
  const s = tot % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
  return `${s}s`;
}

export function formatIctTime(isoUtc: string | null | undefined): string {
  if (!isoUtc) return '—';
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(new Date(isoUtc));
  } catch {
    return isoUtc;
  }
}

export function formatIctShort(isoUtc: string): string {
  try {
    return new Intl.DateTimeFormat('vi-VN', {
      timeZone: 'Asia/Ho_Chi_Minh',
      hour12: false,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    }).format(new Date(isoUtc));
  } catch {
    return isoUtc;
  }
}

export function compareNullableSeconds(
  a: number | null | undefined,
  b: number | null | undefined,
  direction: 'asc' | 'desc',
  idA: string,
  idB: string
): number {
  const aMissing = a === null || a === undefined || Number.isNaN(a);
  const bMissing = b === null || b === undefined || Number.isNaN(b);
  if (aMissing && bMissing) return idA.localeCompare(idB);
  if (aMissing) return 1; // unknown ALWAYS sorts last regardless of direction
  if (bMissing) return -1;
  const diff = direction === 'asc' ? a - b : b - a;
  return diff !== 0 ? diff : idA.localeCompare(idB);
}

export function escapeCsvCell(val: string | number | boolean | null | undefined): string {
  const raw = val === null || val === undefined ? '' : String(val);
  const flattened = raw.replace(/[\r\n]+/g, ' ');
  const neutralized = /^[=+\-@\t]/.test(flattened) ? `'${flattened}` : flattened;
  if (/[",]/.test(neutralized)) {
    return `"${neutralized.replace(/"/g, '""')}"`;
  }
  return neutralized;
}

export function buildWorkloadsCsv(
  rows: SnapshotWorkloadItem[],
  scope: { node_id: string | null; member_id: string | null },
  maxRows = 2000
): string {
  const header = [
    'workload_id',
    'node_id',
    'username',
    'member_id',
    'group_id',
    'workspace',
    'host_pid',
    'display_name',
    'workload_kind',
    'gpu_associated',
    'cpu_percent',
    'memory_bytes',
    'memory_method',
    'vram_bytes',
    'vram_attribution',
    'elapsed_seconds',
    'state',
    'quality',
    'observed_at_utc',
    'scope_node_id',
    'scope_member_id'
  ].join(',');

  const lines = rows
    .slice(0, maxRows)
    .map((r) =>
      [
        r.id,
        r.node_id,
        r.username ?? '',
        r.member_id ?? '',
        r.group_id ?? '',
        r.workspace ?? '',
        r.host_pid,
        r.display_name,
        r.workload_kind,
        r.gpu_associated === null ? '' : String(r.gpu_associated),
        r.cpu.value_percent ?? '',
        r.memory.value_bytes ?? '',
        r.memory.method,
        r.vram.value_bytes ?? '',
        r.vram.attribution,
        r.elapsed_seconds ?? '',
        r.state,
        r.quality,
        r.observed_at ?? '',
        scope.node_id ?? '',
        scope.member_id ?? ''
      ]
        .map(escapeCsvCell)
        .join(',')
    );

  return `${[header, ...lines].join('\n')}\n`;
}

export function buildRangeWindow(preset: HistoryRangePreset): { from: string; to: string } {
  const now = new Date();
  now.setMilliseconds(0);
  const hoursMap: Record<HistoryRangePreset, number> = {
    '1h': 1,
    '6h': 6,
    '24h': 24,
    '7d': 24 * 7,
    '30d': 24 * 30
  };
  const hours = hoursMap[preset] ?? 24;
  const from = new Date(now.getTime() - hours * 3600 * 1000);
  return {
    from: from.toISOString().replace(/\.\d{3}Z$/, 'Z'),
    to: now.toISOString().replace(/\.\d{3}Z$/, 'Z')
  };
}

function buildScopeSearchParams(
  scope: ScopeBase,
  extra?: Record<string, string | undefined>
): string {
  const params = new URLSearchParams();
  if (scope.node_id) params.set('node_id', scope.node_id);
  if (scope.gpu_uuid) params.set('gpu_uuid', scope.gpu_uuid);
  if (scope.member_id) params.set('member_id', scope.member_id);
  if (scope.group_id) params.set('group_id', scope.group_id);
  if (extra) {
    Object.entries(extra).forEach(([k, v]) => {
      if (v !== undefined && v !== '') params.set(k, v);
    });
  }
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

export function assertValidSchemaVersion(
  payload: unknown
): asserts payload is { schema_version: 1 } {
  if (
    !payload ||
    typeof payload !== 'object' ||
    (payload as { schema_version?: unknown }).schema_version !== 1
  ) {
    throw new Error('UNSUPPORTED_SCHEMA_VERSION: Chỉ hỗ trợ contract V3.1 schema_version=1');
  }
}

async function fetchStrictJson<T extends { schema_version: 1 }>(
  url: string,
  signal?: AbortSignal
): Promise<T> {
  const res = await fetch(url, { cache: 'no-store', signal });
  const body = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : (detail as { message?: string } | undefined)?.message || `Lỗi HTTP ${res.status}`;
    throw new Error(msg);
  }
  assertValidSchemaVersion(body);
  return body as T;
}

export async function fetchWorkloadSnapshot(
  scope: ScopeBase,
  signal?: AbortSignal
): Promise<WorkloadSnapshotEnvelope> {
  const qs = buildScopeSearchParams(scope);
  return fetchStrictJson<WorkloadSnapshotEnvelope>(`/api/labops/jobs/snapshot${qs}`, signal);
}

export async function fetchWorkloadAnalytics(
  scope: ScopeBase,
  range: { from: string; to: string; bucket?: number },
  signal?: AbortSignal
): Promise<WorkloadAnalyticsEnvelope> {
  const qs = buildScopeSearchParams(scope, {
    from: range.from,
    to: range.to,
    bucket: range.bucket ? String(range.bucket) : undefined
  });
  return fetchStrictJson<WorkloadAnalyticsEnvelope>(`/api/labops/jobs/analytics${qs}`, signal);
}

export async function fetchWorkloadHistory(
  scope: ScopeBase,
  range: { from: string; to: string; cursor?: string | null; limit?: number },
  signal?: AbortSignal
): Promise<WorkloadHistoryEnvelope> {
  const qs = buildScopeSearchParams(scope, {
    from: range.from,
    to: range.to,
    cursor: range.cursor ?? undefined,
    limit: range.limit ? String(range.limit) : '50'
  });
  return fetchStrictJson<WorkloadHistoryEnvelope>(`/api/labops/jobs/history${qs}`, signal);
}

export async function fetchWorkloadEvents(
  scope: ScopeBase,
  range: { from: string; to: string; cursor?: string | null; limit?: number },
  signal?: AbortSignal
): Promise<WorkloadEventsEnvelope> {
  const qs = buildScopeSearchParams(scope, {
    from: range.from,
    to: range.to,
    cursor: range.cursor ?? undefined,
    limit: range.limit ? String(range.limit) : '50'
  });
  return fetchStrictJson<WorkloadEventsEnvelope>(`/api/labops/jobs/events${qs}`, signal);
}

export async function fetchWorkloadDetail(
  workloadId: string,
  signal?: AbortSignal
): Promise<WorkloadDetailResponse> {
  return fetchStrictJson<WorkloadDetailResponse>(
    `/api/labops/jobs/${encodeURIComponent(workloadId)}/detail`,
    signal
  );
}

export async function executeV31ProcessAction(
  ref: string,
  payload: V31ProcessActionRequest
): Promise<ProcessActionResultEnvelope> {
  const res = await fetch(`/api/labops/processes/${encodeURIComponent(ref)}/v31-action`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const body = (await res.json().catch(() => null)) as unknown;
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const msg =
      typeof detail === 'string'
        ? detail
        : (detail as { message?: string } | undefined)?.message || `Lỗi HTTP ${res.status}`;
    throw new Error(msg);
  }
  assertValidSchemaVersion(body);
  return body as ProcessActionResultEnvelope;
}

const UUID_SEGMENT_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function formatStoragePoolShortLabel(pool: {
  id?: string;
  name?: string;
  label?: string;
  diskIdentifier?: string;
  mountPoint?: string;
}): string {
  const dev = (pool.diskIdentifier || '').replace(/^\/dev\//, '').trim();
  const mount = (pool.mountPoint || pool.name || '').trim();

  if (mount === '/') {
    return dev ? `/ (${dev})` : '/';
  }

  if (mount) {
    const segments = mount.split('/').filter(Boolean);
    const lastSeg = segments[segments.length - 1] || '';
    if (UUID_SEGMENT_RE.test(lastSeg) || /[0-9a-f]{8}-[0-9a-f-]{10,}/i.test(mount)) {
      return dev || `Disk ${lastSeg.slice(0, 6)}`;
    }
    if (mount.startsWith('/mnt/') && mount.length > 5) {
      const mntName = mount.slice(5);
      return dev ? `${mntName} (${dev})` : mntName;
    }
    if (mount.length > 18) {
      return dev || `${mount.slice(0, 15)}…`;
    }
    return dev && !mount.includes(dev) ? `${mount} (${dev})` : mount;
  }

  return dev || (pool.label || pool.id || 'Pool').slice(0, 16);
}

export function formatEntityShortLabel(
  entityId: string | null | undefined,
  nodeId?: string | null
): string {
  const raw = String(entityId ?? '').trim();
  if (!raw) return '—';

  const colonIdx = raw.indexOf(':');
  const rawHost = colonIdx > 0 ? raw.slice(0, colonIdx).trim() : String(nodeId ?? '').trim();
  const idPart = colonIdx > 0 ? raw.slice(colonIdx + 1).trim() : raw;

  const low = idPart.toLowerCase();
  if (low === 'workstation') return 'Workstation';
  if (low === 'track') return 'Server Rack';

  const hostShort =
    rawHost.toLowerCase() === 'workstation'
      ? 'WS'
      : rawHost.toLowerCase() === 'track'
        ? 'Rack'
        : rawHost;

  const gpuMatch = idPart.match(/^GPU-([0-9a-f]{4,})/i);
  if (gpuMatch) {
    const shortHex = gpuMatch[1].slice(0, 4).toLowerCase();
    const gpuTag = `A30 #${shortHex}`;
    return hostShort ? `${hostShort} • ${gpuTag}` : gpuTag;
  }

  if (idPart.length > 18) {
    const compact = `${idPart.slice(0, 12)}…`;
    return hostShort ? `${hostShort} • ${compact}` : compact;
  }

  return hostShort ? `${hostShort} • ${idPart}` : idPart;
}

export function formatGroupAttributionMode(mode?: string | null): string {
  switch (String(mode || '').toUpperCase()) {
    case 'CURRENT_MEMBER_PROFILE':
      return 'Hồ sơ thành viên hiện tại';
    case 'HISTORICAL_SNAPSHOT':
      return 'Dữ liệu lịch sử tại thời điểm chạy';
    case 'UNVERIFIED':
    case '':
      return 'Chưa xác minh';
    default:
      return String(mode);
  }
}

export function formatWorkloadKindLabel(kind?: string | null): string {
  switch (String(kind || '').toUpperCase()) {
    case 'RESEARCH_TRAINING':
      return 'Huấn luyện AI/ML';
    case 'GENERAL_COMPUTE':
      return 'Tính toán chung';
    case 'INTERACTIVE_JUPYTER':
      return 'Jupyter / Notebook';
    case 'DATA_PREPROCESSING':
      return 'Tiền xử lý dữ liệu';
    case 'INFERENCE_SERVICE':
      return 'Dịch vụ Inference';
    case 'MONITORING':
      return 'Giám sát / Hệ thống';
    case 'SYSTEM_BACKGROUND':
      return 'Tác vụ nền hệ thống';
    case 'UNKNOWN':
    case '':
      return 'Chưa phân loại';
    default:
      return String(kind);
  }
}

export function formatKindConfidenceLabel(conf?: string | null): string {
  switch (String(conf || '').toUpperCase()) {
    case 'HIGH':
    case 'VERIFIED':
      return 'Tin cậy cao';
    case 'MEDIUM':
      return 'Trung bình';
    case 'LOW':
      return 'Thấp';
    case 'HEURISTIC':
      return 'Suy luận từ lệnh';
    default:
      return String(conf || 'Chưa rõ');
  }
}

export function formatCpuBasisLabel(basis?: string | null): string {
  switch (String(basis || '').toUpperCase()) {
    case 'HOST_CAPACITY_PERCENT':
      return 'Theo tổng CPU máy chủ';
    case 'CORE_NORMALIZED':
      return 'Chuẩn hóa theo số nhân';
    case 'UNKNOWN':
    case '':
      return 'Chưa xác định';
    default:
      return String(basis);
  }
}

export function formatMemoryMethodLabel(method?: string | null): string {
  switch (String(method || '').toUpperCase()) {
    case 'TREE_RSS_SUM':
      return 'Tổng RSS cây tiến trình';
    case 'PROCESS_RSS':
      return 'RSS tiến trình đơn lẻ';
    case 'UNKNOWN':
    case '':
      return 'Chưa xác định';
    default:
      return String(method);
  }
}

export function formatVramAttributionLabel(attr?: string | null): string {
  switch (String(attr || '').toUpperCase()) {
    case 'LEGACY_AGGREGATE':
      return 'Tổng hợp quy trình / GPU';
    case 'PER_DEVICE_VERIFIED':
      return 'Xác minh NVML từng GPU';
    case 'NONE':
    case '':
      return 'Không sử dụng GPU';
    default:
      return String(attr);
  }
}

export function formatStateBadgeLabel(val?: string | null): string {
  switch (String(val || '').toUpperCase()) {
    case 'OBSERVED':
      return 'Đang chạy';
    case 'FRESH':
      return 'Trực tiếp';
    case 'STALE':
      return 'Dữ liệu cũ';
    case 'OFFLINE':
      return 'Ngoại tuyến';
    case 'MISSING_UNCONFIRMED':
      return 'Đã ngừng / Mất dấu';
    case 'EXIT_CONFIRMED':
      return 'Đã kết thúc';
    case 'ERROR':
      return 'Lỗi thu thập';
    case 'UNKNOWN':
    case '':
      return 'Chưa xác minh';
    default:
      return String(val);
  }
}

export function formatActionBlockReasonLabel(reason?: string | null): string {
  if (!reason) return '';
  if (reason.includes('disabled by feature flag')) {
    return 'Thao tác tiến trình V3.1 đang tạm khóa theo cấu hình an toàn.';
  }
  if (reason.includes('destructive controls disabled on stale data')) {
    return 'Dữ liệu tiến trình đang cũ (STALE) — tạm khóa thao tác dừng tiến trình.';
  }
  if (reason.includes('Trusted boot_id')) {
    return 'Thiếu định danh xác thực (boot_id / ProcessRef / start_ticks) để thao tác an toàn.';
  }
  return reason;
}
