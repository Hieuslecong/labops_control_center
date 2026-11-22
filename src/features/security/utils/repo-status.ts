import type {
  RepositoryCloneStatus,
  RepositoryGuardRequest,
  RepositoryPostCloneReviewStatus
} from '@/types/labops';

export function hasPostCloneFindings(req: RepositoryGuardRequest): boolean {
  const report = req.report;
  if (!report) return false;
  return (
    (report.secrets_count ?? 0) > 0 ||
    (report.vulnerabilities?.critical ?? 0) > 0 ||
    (report.vulnerabilities?.high ?? 0) > 0 ||
    (report.misconfig_high_count ?? 0) > 0 ||
    (report.coverage?.overall_coverage ?? 'POLICY_COMPLETE') !== 'POLICY_COMPLETE'
  );
}

export function resolveCloneStatus(req: RepositoryGuardRequest): RepositoryCloneStatus {
  if (req.clone_status) return req.clone_status;
  if (req.source === 'workspace_clone') return 'CLONED';

  const state = String(req.state || '').toUpperCase();
  switch (state) {
    case 'AUTO_APPROVED':
    case 'APPROVED':
    case 'MANUALLY_APPROVED':
    case 'DELIVERED':
      return 'CLONED';
    case 'REQUESTED':
    case 'SUBMITTED':
    case 'VALIDATING':
    case 'RESOLVING_COMMIT':
    case 'CHECKING_CACHE':
    case 'FETCHING':
    case 'SCANNING':
    case 'POLICY_EVALUATION':
    case 'PUBLISHING':
    case 'DELIVERING':
      return 'CLONING';
    case 'REJECTED':
    case 'REVOKED':
      return 'BLOCKED';
    case 'REVIEW_REQUIRED':
    case 'MANUAL_REVIEW':
      return 'PENDING';
    case 'FAILED':
    default:
      return 'FAILED';
  }
}

export function resolvePostCloneReviewStatus(
  req: RepositoryGuardRequest
): RepositoryPostCloneReviewStatus {
  if (req.review_status) return req.review_status;

  if (req.decision) {
    const v = String(req.decision.verdict || '').toUpperCase();
    if (v === 'REJECTED') return 'REJECTED';
    if (v === 'APPROVED' || v === 'MANUALLY_APPROVED') return 'APPROVED';
  }

  const state = String(req.state || '').toUpperCase();
  switch (state) {
    case 'REVIEW_REQUIRED':
    case 'MANUAL_REVIEW':
      return 'REVIEW_REQUIRED';
    case 'REJECTED':
    case 'REVOKED':
      return 'REJECTED';
    case 'MANUALLY_APPROVED':
    case 'APPROVED':
      return 'APPROVED';
    case 'AUTO_APPROVED':
    case 'DELIVERED':
      return hasPostCloneFindings(req) ? 'REVIEW_REQUIRED' : 'CLEAN';
    case 'SCANNING':
    case 'POLICY_EVALUATION':
    case 'FETCHING':
    case 'RESOLVING_COMMIT':
      return 'SCANNING';
    case 'FAILED':
      return resolveCloneStatus(req) === 'CLONED' ? 'SCAN_FAILED' : 'NOT_STARTED';
    default:
      return 'NOT_STARTED';
  }
}

export function getCloneStatusMeta(status: RepositoryCloneStatus): {
  label: string;
  className: string;
} {
  switch (status) {
    case 'CLONED':
      return {
        label: 'Thành công',
        className: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30'
      };
    case 'CLONING':
      return {
        label: 'Đang clone',
        className: 'bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30 animate-pulse'
      };
    case 'PENDING':
      return {
        label: 'Chờ duyệt clone',
        className: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30'
      };
    case 'BLOCKED':
      return {
        label: 'Bị chặn',
        className: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30'
      };
    case 'FAILED':
    default:
      return {
        label: 'Thất bại',
        className: 'bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30'
      };
  }
}

export function getPostCloneReviewMeta(status: RepositoryPostCloneReviewStatus): {
  label: string;
  className: string;
} {
  switch (status) {
    case 'CLEAN':
      return {
        label: 'Đạt (Sạch)',
        className: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30'
      };
    case 'APPROVED':
      return {
        label: 'Admin đã duyệt',
        className: 'bg-teal-500/15 text-teal-700 dark:text-teal-400 border-teal-500/30'
      };
    case 'REVIEW_REQUIRED':
      return {
        label: 'Cần kiểm duyệt',
        className: 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30'
      };
    case 'REJECTED':
      return {
        label: 'Không đạt / Từ chối',
        className: 'bg-rose-500/15 text-rose-700 dark:text-rose-400 border-rose-500/30'
      };
    case 'SCANNING':
      return {
        label: 'Đang quét',
        className: 'bg-sky-500/15 text-sky-700 dark:text-sky-400 border-sky-500/30 animate-pulse'
      };
    case 'SCAN_FAILED':
      return {
        label: 'Lỗi quét hậu kiểm',
        className: 'bg-orange-500/15 text-orange-700 dark:text-orange-400 border-orange-500/30'
      };
    case 'NOT_STARTED':
    default:
      return {
        label: 'Chưa quét',
        className: 'bg-muted text-muted-foreground border-border'
      };
  }
}
