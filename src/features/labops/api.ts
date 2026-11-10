import { toast } from 'sonner';

/** Kết quả gọi backend LabOps qua BFF `/api/labops/*`. Không bao giờ giả thành công. */
export interface LabOpsCallResult<T = unknown> {
  ok: boolean;
  status: number;
  data: T | null;
  error?: string;
}

export async function labopsCall<T = unknown>(
  path: string,
  init: { method?: string; body?: unknown; idempotent?: boolean } = {}
): Promise<LabOpsCallResult<T>> {
  try {
    const res = await fetch(`/api/labops/${path.replace(/^\//, '')}`, {
      method: init.method ?? 'GET',
      headers: {
        ...(init.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(init.idempotent ? { 'Idempotency-Key': crypto.randomUUID() } : {})
      },
      ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {})
    });
    const data = (await res.json().catch(() => null)) as T | null;
    if (!res.ok) {
      const detail = (data as { detail?: unknown } | null)?.detail;
      const msg =
        typeof detail === 'string'
          ? detail
          : (detail as { message?: string } | undefined)?.message || `Lỗi HTTP ${res.status}`;
      return { ok: false, status: res.status, data, error: msg };
    }
    return { ok: true, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null, error: 'Không kết nối được backend LabOps' };
  }
}

/** Hiển thị thông báo trung thực theo trạng thái xác minh từ backend. */
export function toastVerification(
  title: string,
  result: LabOpsCallResult<{ verification?: string; state?: string }>
): void {
  if (!result.ok) {
    toast.error(`${title}: thất bại`, { description: result.error });
    return;
  }
  const v = result.data?.verification;
  if (v === 'VERIFIED') {
    toast.success(`${title}: đã xác minh`, { description: `Trạng thái: ${result.data?.state}` });
  } else {
    toast.info(`${title}: đã gửi yêu cầu, chờ xác minh`, {
      description: `Trạng thái: ${result.data?.state ?? 'PENDING_VERIFICATION'}`
    });
  }
}

