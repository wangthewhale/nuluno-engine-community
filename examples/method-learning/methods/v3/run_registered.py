#!/usr/bin/env python3
"""Keep failed/unavailable registered sources in the public denominator.

Host orchestration only. It does not alter the frozen candidate or Engine
adapter, and does not turn a failed fetch into a synthetic successful case.
"""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from demo import save, sha, check_freeze
from engine_demo import checked, run


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--registration', required=True)
    p.add_argument('--freeze', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    checked(a.freeze)
    check_freeze(Path(__file__).resolve().parent / 'candidate-freeze.json')
    registration = json.loads(Path(a.registration).read_text())
    if registration.get('method_freeze_sha256') != sha(Path(a.freeze).read_bytes()):
        raise ValueError('registered_method_freeze_mismatch')
    manifest = json.loads(Path(a.manifest).read_text())
    expected = {row['id']: row['url'] for row in registration['sources']}
    sources = manifest['sources']
    if len(sources) != len(expected) or {row['id'] for row in sources} != set(expected):
        raise ValueError('all_original_registered_sources_required')
    unavailable = []
    for row in sources:
        source_path, oracle_path = Path(row['source']), Path(row['oracle'])
        if not source_path.is_file():
            unavailable.append({'id': row['id'], 'requested_url': expected[row['id']], 'reason': 'original_source_unavailable'})
            continue
        source = json.loads(source_path.read_text())
        if source.get('requested_url', source['url']) != expected[row['id']]:
            raise ValueError('registered_source_was_replaced')
        if not oracle_path.is_file():
            unavailable.append({'id': row['id'], 'requested_url': expected[row['id']], 'reason': 'independent_oracle_unavailable'})
    if unavailable:
        report = {'schema': 'nuluno-engine-method-demo-v1', 'state': 'rejected_before_native_adoption',
                  'reason': 'complete_registered_evaluation_not_available', 'source_denominator': len(expected),
                  'unavailable': unavailable, 'native_candidate_executed': False,
                  'original_head_unchanged': True, 'economic_adoption_proven': False,
                  'method_freeze_sha256': sha(Path(a.freeze).read_bytes()),
                  'registration_sha256': sha(Path(a.registration).read_bytes()),
                  'orchestration_wrapper_sha256': sha(Path(__file__).read_bytes()),
                  'sales_result': None, 'complete_cost': None}
        save(Path(a.out) / 'engine-observation.json', report)
        print(json.dumps(report, sort_keys=True))
        return
    run(SimpleNamespace(freeze=a.freeze, manifest=a.manifest, out=a.out))


if __name__ == '__main__': main()
