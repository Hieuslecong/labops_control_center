import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  extractV4ErrorMessage,
  formatV4Scalar,
  normalizeOutboxSummary
} from '../features/ai-assistant/components/assistant-v4-panel';

describe('Assistant V4 Panel Safe Serialization & Outbox Normalization', () => {
  it('safely extracts scalar and nested by_status counters from V4 /health and /metrics outbox payloads', () => {
    const normalized = normalizeOutboxSummary({
      total: 3,
      backlog_count: 1,
      oldest_pending_age_seconds: 12.5,
      by_status: {
        SENT: 2,
        PENDING: 1
      },
      oldest_pending_age_seconds_by_priority: {
        P1: 12.5
      }
    });

    assert.equal(normalized.total, 3);
    assert.equal(normalized.backlogCount, 1);
    assert.equal(normalized.oldestPendingAgeSeconds, 12.5);
    assert.deepEqual(normalized.byStatus, [
      ['SENT', 2],
      ['PENDING', 1]
    ]);
    assert.deepEqual(normalized.oldestByPriority, [['P1', 12.5]]);
  });

  it('handles empty or null outbox objects cleanly without throwing', () => {
    assert.deepEqual(normalizeOutboxSummary(null), {
      total: 0,
      backlogCount: 0,
      oldestPendingAgeSeconds: 0,
      byStatus: [],
      oldestByPriority: []
    });
    assert.deepEqual(
      normalizeOutboxSummary({
        total: 0,
        by_status: {},
        oldest_pending_age_seconds_by_priority: {}
      }),
      {
        total: 0,
        backlogCount: 0,
        oldestPendingAgeSeconds: 0,
        byStatus: [],
        oldestByPriority: []
      }
    );
  });

  it('never passes raw objects through formatV4Scalar or extractV4ErrorMessage', () => {
    assert.equal(formatV4Scalar(null), '—');
    assert.equal(formatV4Scalar(42), '42');
    assert.equal(formatV4Scalar({ nested: true }), '{"nested":true}');
    assert.equal(formatV4Scalar(['a', { b: 1 }]), 'a, {"b":1}');

    assert.equal(
      extractV4ErrorMessage({ detail: [{ msg: 'Field required' }] }, 422),
      'Field required'
    );
    assert.equal(
      extractV4ErrorMessage({ detail: { code: 'E_TEST', message: 'Invalid state' } }, 400),
      'E_TEST: Invalid state'
    );
    assert.equal(extractV4ErrorMessage({}, 500), 'HTTP 500');
  });
});
