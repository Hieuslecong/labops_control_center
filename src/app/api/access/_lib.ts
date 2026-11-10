import { proxyLabOps } from '../labops/_lib';

export async function proxyLabOpsAccess(request: Request, path: string): Promise<Response> {
  const segments = path.split('/').filter(Boolean);
  return proxyLabOps(request, segments, { requireAdmin: true });
}
