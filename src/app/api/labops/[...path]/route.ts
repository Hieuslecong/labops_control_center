import { proxyLabOps } from '../_lib';

export const dynamic = 'force-dynamic';

interface RouteContext {
  params: Promise<{ path: string[] }>;
}

async function handle(request: Request, { params }: RouteContext): Promise<Response> {
  const { path } = await params;
  return proxyLabOps(request, path);
}

export { handle as GET, handle as POST, handle as PUT, handle as PATCH, handle as DELETE };

