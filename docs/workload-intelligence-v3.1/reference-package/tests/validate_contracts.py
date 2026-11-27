"""Offline package acceptance: JSON schema and semantic invariants. Not application tests."""
from pathlib import Path
from datetime import datetime
import json
from jsonschema import Draft202012Validator, FormatChecker
ROOT=Path(__file__).resolve().parent.parent

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def ts(s): return datetime.fromisoformat(s.replace('Z','+00:00'))

def check_snapshot(s):
    rows=s['workloads']; q=s['quality']; k=s['kpis']; nodes=s['nodes']; gpus=s['gpus']
    assert len({r['id'] for r in rows})==len(rows)
    assert len({n['id'] for n in nodes})==len(nodes)
    assert len({g['gpu_uuid'] for g in gpus})==len(gpus)
    assert all(r['node_id'] in {n['id'] for n in nodes} for r in rows)
    assert q['fresh_nodes']==sum(n['quality']=='FRESH' for n in nodes)
    assert q['fresh_workload_nodes']==sum(n['workload_quality']=='FRESH' for n in nodes)
    fresh=[r for r in rows if r['quality']=='FRESH']; stale=[r for r in rows if r['quality']=='STALE']
    assert all(r['quality']!='FRESH' or r['observed_at'] for r in rows)
    assert all(not r['actions_allowed'] for r in rows), 'fixtures must not be actionable'
    assert all(not r['is_protected'] and r['workload_kind']!='SYSTEM' for r in rows)
    assert k['last_known_workloads']==(len(stale) if stale else None)
    if not q['fresh_workload_nodes']:
        for m in ['observed_workloads','gpu_associated_workloads','gpu_association_unknown','observed_members','unmapped_member_workloads']:assert k[m] is None
        assert s['composition'] is None and not fresh
    else:
        assert k['observed_workloads']==len(fresh)
        assert k['gpu_associated_workloads']==sum(r['gpu_associated'] is True for r in fresh)
        assert k['gpu_association_unknown']==sum(r['gpu_associated'] is None for r in fresh)
        assert k['observed_members']==len({r['member_id'] for r in fresh if r['member_id']})
        assert k['unmapped_member_workloads']==sum(r['member_id'] is None for r in fresh)
        comp=dict.fromkeys(['gpu_non_monitoring','non_gpu_compute','monitoring','other_unknown'],0)
        for r in fresh:
            kind=r['workload_kind']; g=r['gpu_associated']; key='monitoring' if kind=='MONITORING' else 'gpu_non_monitoring' if g is True else 'non_gpu_compute' if g is False and kind in {'TRAINING','INFERENCE','NOTEBOOK','GENERAL_COMPUTE'} else 'other_unknown';comp[key]+=1
        assert s['composition']==comp
    assert q['fresh_gpus']==sum(g['quality']=='FRESH' for g in gpus)==k['gpu_coverage']['fresh']
    assert q['expected_gpus']==k['gpu_coverage']['expected']
    for a,b in [('fresh_gpus','expected_gpus'),('fresh_nodes','expected_nodes')]:assert q[b] is None or q[a]<=q[b]
    pairs=[g for g in gpus if g['quality']=='FRESH' and g['vram_used_bytes'] is not None and g['vram_capacity_bytes'] is not None]
    assert k['device_vram_used_bytes']==(sum(g['vram_used_bytes'] for g in pairs) if pairs else None)
    assert k['device_vram_capacity_bytes']==(sum(g['vram_capacity_bytes'] for g in pairs) if pairs else None)
    assert all(g['vram_used_bytes']<=g['vram_capacity_bytes'] for g in pairs)
    for r in rows:
        a=r['gpu_allocations'];
        if a is not None:
            assert len({x['gpu_uuid'] for x in a})==len(a)
            assert all(x['gpu_uuid'] in {g['gpu_uuid'] for g in gpus} for x in a)
            assert sum(x['value_bytes'] for x in a)==r['vram']['value_bytes']
    if s['snapshot_id']=='fixture-empty': assert k['observed_workloads']==0
    if s['snapshot_id']=='fixture-offline': assert k['observed_workloads'] is None

def main():
    mappings={n:'workload-snapshot' for n in ['healthy','partial-coverage','offline-node','stale-snapshot','empty-workloads','multi-gpu']}
    mappings.update(analytics='workload-analytics',history='workload-history',events='workload-events',**{'action-partial':'process-action-result'})
    for n,schema in mappings.items():
        data=load(ROOT/'fixtures'/f'{n}.json'); spec=load(ROOT/'contracts'/f'{schema}.schema.json'); Draft202012Validator.check_schema(spec); Draft202012Validator(spec,format_checker=FormatChecker()).validate(data)
        if schema=='workload-snapshot':check_snapshot(data)
        if n=='analytics':
            for series in data['series']:
                points=series['points'];times=[ts(x['ts']) for x in points];assert times==sorted(set(times));assert all(ts(data['from'])<=t<ts(data['to']) for t in times);assert all(x['quality']!='NO_DATA' or x['value'] is None for x in points)
        if n=='history':assert all(x['state']=='EXIT_CONFIRMED' or x['exit_code'] is None for x in data['items']); assert all(not x['actionable'] for x in data['items'])
        if n=='action-partial':assert data['status']=='PARTIAL_FAILURE' and data['verification']!='VERIFIED' and data['pids_remaining']
        print('PASS',n)
    # Negative fixture: schema-valid but incoherent KPI must fail semantic check.
    bad=load(ROOT/'fixtures'/'partial-coverage.json');bad['kpis']['observed_workloads']=999
    try:check_snapshot(bad)
    except AssertionError:print('PASS negative count invariant')
    else:raise AssertionError('inconsistent KPI accepted')
    print('PASS 10 fixture validations + 1 negative invariant')
if __name__=='__main__':main()
