import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { isAdminRequiredLabOpsPath } from '../app/api/labops/_lib';
import {
  assertValidSchemaVersion,
  buildWorkloadsCsv,
  compareNullableSeconds,
  escapeCsvCell,
  formatBytesGiB,
  formatDurationSeconds,
  formatEntityShortLabel,
  formatIctTime,
  stableEntityColor
} from '../features/jobs/api/service';
import { buildMetricTrendModel } from '../features/overview/components/gpu-hours-chart';
import type { SnapshotWorkloadItem } from '../features/jobs/api/types';

const THIS_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(THIS_DIR, '../..');

function makeSampleWorkload(overrides: Partial<SnapshotWorkloadItem> = {}): SnapshotWorkloadItem {
  return {
    id: 'wl-workstation-101',
    ref: 'workstation:host:101:555',
    node_id: 'workstation',
    boot_id: 'boot-1234',
    host_pid: 101,
    start_ticks: 555,
    container_id: null,
    uid: 1001,
    member_id: 'mem-1',
    username: 'vinh_test',
    group_id: 'AI Systems',
    workspace: '/srv/ws/vinh',
    display_name: 'python train.py',
    workload_kind: 'TRAINING',
    kind_confidence: 'HEURISTIC',
    kind_evidence: ['cmdline:train.py'],
    gpu_associated: true,
    cpu: { value_percent: 45.2, basis: 'PER_CORE_PERCENT' },
    memory: { value_bytes: 2 * 1024 ** 3, method: 'TREE_RSS_SUM', unique_memory: false },
    vram: { value_bytes: 8 * 1024 ** 3, attribution: 'PER_DEVICE_VERIFIED' },
    gpu_allocations: [{ gpu_uuid: 'GPU-WS-01', value_bytes: 8 * 1024 ** 3, quality: 'FRESH' }],
    elapsed_seconds: 3665,
    started_at: '2026-11-25T10:00:00Z',
    state: 'OBSERVED',
    quality: 'FRESH',
    observed_at: '2026-11-25T11:01:05Z',
    is_protected: false,
    actions_allowed: true,
    action_block_reason: null,
    ...overrides
  };
}

describe('LabOps Workload Intelligence V3.1 Unit & Security Gates', () => {
  it('enforces admin-only BFF RBAC on all V3.1 workload endpoints while keeping researcher endpoints intact', () => {
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'snapshot']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'snapshot', 'export.csv']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'analytics']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'history']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'events']), true);
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs', 'wl-ws-101', 'detail']), true);
    assert.equal(
      isAdminRequiredLabOpsPath('POST', ['processes', 'workstation:host:101:555', 'v31-action']),
      true
    );

    // Researcher-compatible paths must remain accessible per backend self-scope rules
    assert.equal(isAdminRequiredLabOpsPath('GET', ['jobs']), false);
    assert.equal(
      isAdminRequiredLabOpsPath('GET', ['processes', 'workstation:host:101:555', 'tree']),
      false
    );
  });

  it('neutralizes CSV spreadsheet formula injection and flattens newlines', () => {
    assert.equal(escapeCsvCell('=CMD|calc'), "'=CMD|calc");
    assert.equal(escapeCsvCell('+1+1'), "'+1+1");
    assert.equal(escapeCsvCell('-100'), "'-100");
    assert.equal(escapeCsvCell('@SUM(A1:A2)'), "'@SUM(A1:A2)");
    assert.equal(escapeCsvCell('\t=2+2'), "'\t=2+2");
    assert.equal(escapeCsvCell('line1\n=evil\r\nok'), 'line1 =evil ok');
    assert.equal(escapeCsvCell('normal,"quoted"'), '"normal,""quoted"""');

    const evilRow = makeSampleWorkload({ display_name: '=HYPERLINK("http://evil","x")' });
    const csv = buildWorkloadsCsv([evilRow], { node_id: 'workstation', member_id: null });
    assert.ok(csv.includes(`"'=HYPERLINK(""http://evil"",""x"")"`));
    assert.ok(csv.includes(',workstation,'));
  });

  it('sorts numeric elapsed_seconds with unknown (null) strictly last in both asc and desc', () => {
    const knownShort = { id: 'a', elapsed_seconds: 30 };
    const knownLong = { id: 'b', elapsed_seconds: 900 };
    const unknown = { id: '0', elapsed_seconds: null };

    const asc = [unknown, knownLong, knownShort].toSorted((x, y) =>
      compareNullableSeconds(x.elapsed_seconds, y.elapsed_seconds, 'asc', x.id, y.id)
    );
    assert.deepEqual(
      asc.map((r) => r.id),
      ['a', 'b', '0']
    );

    const desc = [unknown, knownShort, knownLong].toSorted((x, y) =>
      compareNullableSeconds(x.elapsed_seconds, y.elapsed_seconds, 'desc', x.id, y.id)
    );
    assert.deepEqual(
      desc.map((r) => r.id),
      ['b', 'a', '0']
    );
  });

  it('formats nullable telemetry values safely with em-dash and rejects bad schema versions', () => {
    assert.equal(formatBytesGiB(null), '—');
    assert.equal(formatBytesGiB(undefined), '—');
    assert.equal(formatBytesGiB(1024 ** 3), '1.0 GiB');
    assert.equal(formatDurationSeconds(null), '—');
    assert.equal(formatDurationSeconds(3665), '1h 01m');
    assert.equal(formatIctTime(null), '—');
    assert.equal(stableEntityColor('mem-1'), stableEntityColor('mem-1'));

    assert.doesNotThrow(() => assertValidSchemaVersion({ schema_version: 1 }));
    assert.throws(
      () => assertValidSchemaVersion({ schema_version: 2 }),
      /UNSUPPORTED_SCHEMA_VERSION/
    );
    assert.throws(() => assertValidSchemaVersion(null), /UNSUPPORTED_SCHEMA_VERSION/);
  });

  it('keeps V3.1 components compliant with LabOps static UI constraints', () => {
    const v31Files = [
      'src/features/jobs/components/workload-kpi-grid.tsx',
      'src/features/jobs/components/workload-charts.tsx',
      'src/features/jobs/components/workload-table-and-drawer.tsx',
      'src/features/jobs/components/workload-intelligence-v31.tsx',
      'src/features/jobs/components/jobs-monitoring-view.tsx'
    ];
    const forbiddenTimerCall = ['set', 'Interval('].join('');
    for (const rel of v31Files) {
      const content = fs.readFileSync(path.join(ROOT_DIR, rel), 'utf8');
      assert.equal(content.includes(forbiddenTimerCall), false, `Forbidden timer loop in ${rel}`);
      assert.equal(
        content.includes('@tabler/icons-react'),
        false,
        `Forbidden @tabler/icons-react in ${rel}`
      );
      assert.equal(
        /text-\[(?:8|9|10|11)px\]/.test(content),
        false,
        `Forbidden micro-font size in ${rel}`
      );
    }
  });

  it('formats GPU UUIDs cleanly into concise labels and assigns distinct colors per telemetry series', () => {
    assert.equal(formatEntityShortLabel('GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc'), 'A30 #1f6b');
    assert.equal(
      formatEntityShortLabel('GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc', 'workstation'),
      'WS • A30 #1f6b'
    );
    assert.equal(formatEntityShortLabel('workstation'), 'Workstation');
    assert.equal(formatEntityShortLabel('track'), 'Server Rack');

    const model = buildMetricTrendModel(
      {
        schema_version: 1,
        scope: { node_id: null, gpu_uuid: null, member_id: null, group_id: null },
        from: '2026-11-26T03:00:00Z',
        to: '2026-11-26T09:00:00Z',
        bucket_seconds: 300,
        coverage_ratio: 1,
        capabilities: {},
        series: [
          {
            metric: 'gpu_utilization_percent',
            entity_id: 'GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc',
            unit: '%',
            aggregation: 'avg',
            coverage_ratio: 1,
            points: [{ ts: '2026-11-26T08:00:00Z', value: 99, quality: 'FRESH' }]
          },
          {
            metric: 'gpu_vram_utilization_percent',
            entity_id: 'GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc',
            unit: '%',
            aggregation: 'avg',
            coverage_ratio: 1,
            points: [{ ts: '2026-11-26T08:00:00Z', value: 3.3, quality: 'FRESH' }]
          },
          {
            metric: 'host_cpu_percent',
            entity_id: 'workstation',
            unit: '%',
            aggregation: 'avg',
            coverage_ratio: 1,
            points: [{ ts: '2026-11-26T08:00:00Z', value: 21.36, quality: 'FRESH' }]
          }
        ]
      },
      ['gpu_utilization_percent', 'gpu_vram_utilization_percent', 'host_cpu_percent']
    );

    const gpuCfg = model.config['gpu_utilization_percent:GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc'];
    const vramCfg =
      model.config['gpu_vram_utilization_percent:GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc'];
    const cpuCfg = model.config['host_cpu_percent:workstation'];
    assert.equal(gpuCfg?.label, 'GPU Compute % (A30 #1f6b)');
    assert.equal(vramCfg?.label, 'VRAM % (A30 #1f6b)');
    assert.equal(cpuCfg?.label, 'CPU % (Workstation)');
    assert.notEqual(gpuCfg?.color, cpuCfg?.color);
    assert.notEqual(gpuCfg?.color, vramCfg?.color);
    assert.notEqual(vramCfg?.color, cpuCfg?.color);
  });
});
