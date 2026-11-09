import { proxyLabOpsAccess } from '../../../../../_lib';

type RouteContext = { params: Promise<{ id: string; deviceId: string }> };

export async function DELETE(request: Request, { params }: RouteContext) {
  const { id, deviceId } = await params;
  return proxyLabOpsAccess(
    request,
    `users/${encodeURIComponent(id)}/wireguard/devices/${encodeURIComponent(deviceId)}`
  );
}
