import { proxyLabOpsAccess } from '../../../_lib';

type RouteContext = { params: Promise<{ id: string }> };

export async function POST(request: Request, { params }: RouteContext) {
  const { id } = await params;
  return proxyLabOpsAccess(request, `users/${encodeURIComponent(id)}/revoke`);
}
