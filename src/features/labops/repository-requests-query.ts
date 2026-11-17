import { queryOptions } from '@tanstack/react-query';
import { labopsCall } from '@/features/labops/api';
import type { RepositoryGuardRequest } from '@/types/labops';

export const repositoryRequestsQuery = () =>
  queryOptions({
    queryKey: ['labops', 'repositories', 'requests'] as const,
    queryFn: async (): Promise<RepositoryGuardRequest[]> => {
      const result = await labopsCall<RepositoryGuardRequest[]>('repositories/requests');
      if (!result.ok) {
        throw new Error(result.error || 'Không thể tải yêu cầu repository');
      }
      if (!Array.isArray(result.data)) {
        throw new Error('Backend trả về danh sách yêu cầu repository không hợp lệ');
      }
      return result.data;
    }
  });
