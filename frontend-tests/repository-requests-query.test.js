import { afterEach, describe, expect, it } from 'bun:test';
import { repositoryRequestsQuery } from '../src/features/labops/repository-requests-query';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('repository requests query', () => {
  it('loads only a validated request list from the LabOps BFF', async () => {
    let requestedPath = '';
    globalThis.fetch = async (input) => {
      requestedPath = String(input);
      return Response.json([{ id: 'request-test-1', state: 'REVIEW_REQUIRED' }]);
    };

    const query = repositoryRequestsQuery();
    const requests = await query.queryFn();

    expect(requestedPath).toBe('/api/labops/repositories/requests');
    expect(requests).toEqual([{ id: 'request-test-1', state: 'REVIEW_REQUIRED' }]);
    expect(query.queryKey).toEqual(['labops', 'repositories', 'requests']);
  });

  it('rejects backend failures instead of caching them as an empty success', async () => {
    globalThis.fetch = async () =>
      Response.json({ detail: 'backend unavailable' }, { status: 503 });

    await expect(repositoryRequestsQuery().queryFn()).rejects.toThrow('backend unavailable');
  });

  it('rejects a response that is not an array', async () => {
    globalThis.fetch = async () => Response.json({ id: 'not-a-list' });

    await expect(repositoryRequestsQuery().queryFn()).rejects.toThrow(
      'Backend trả về danh sách yêu cầu repository không hợp lệ'
    );
  });
});
