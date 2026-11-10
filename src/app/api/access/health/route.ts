import { proxyLabOpsAccess } from '../_lib';

export const GET = (request: Request) => proxyLabOpsAccess(request, 'access/health');
