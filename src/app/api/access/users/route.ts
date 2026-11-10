import { proxyLabOpsAccess } from '../_lib';

export const GET = (request: Request) => proxyLabOpsAccess(request, 'users');
export const POST = (request: Request) => proxyLabOpsAccess(request, 'users');
