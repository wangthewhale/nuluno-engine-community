#!/usr/bin/env python3
"""Acquire a historical public source only if its recorded hashes still match.

No original snapshots are redistributed. A changed/unavailable source stays in
the registered denominator, and historical oracle claims are never rebound.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--method', choices=('v1', 'v2'), required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    method = HERE / 'methods' / a.method
    sys.path.insert(0, str(method))
    from demo import fetch, save
    descriptors = json.loads((method / 'evidence/source-descriptors.json').read_text())['sources']
    registered = json.loads((method / 'evidence/heldout-registration.json').read_text())
    expected = {row['id']: row['url'] for row in registered['sources']}
    if {r['id'] for r in descriptors} != set(expected):
        raise ValueError('all_original_cases_required')
    out = Path(a.out).resolve()
    if out.exists():
        raise ValueError('keep_original_receipts_use_new_output')
    out.mkdir(parents=True)

    def collect(row):
        ident = row['id']
        folder = out / 'private' / ident
        folder.mkdir(parents=True)
        report = {'id':ident, 'original_requested_url':expected[ident],
                  'fetch_url':row['replay_fetch_url'], 'candidate_executed':False,
                  'baseline_executed':False}
        try:
            actual, raw, elapsed = fetch(row['replay_fetch_url'])
            (folder / 'source.html').write_bytes(raw)
            body_match = actual['body_sha256'] == row['historical_source_fields']['body_sha256']
            text_match = hashlib.sha256(actual['text'].encode()).hexdigest() == row['historical_text_sha256']
            report.update(status=actual['status'], body_sha256=actual['body_sha256'],
                          historical_body_hash_matches=body_match,
                          historical_text_hash_matches=text_match,
                          provider_gets=actual['provider_gets'], fetch_ms=elapsed)
            if body_match and text_match:
                # Recreate the historical byte-budget envelope only after both
                # original body and extracted text hashes match. This timestamp
                # is not represented as the time of the new provider GET.
                source = dict(row['historical_source_fields'])
                source['text'] = actual['text']
                source['requested_url'] = expected[ident]
                save(folder / 'source.json', source)
                report['state'] = 'historical_source_hash_verified'
            else:
                report['state'] = 'source_changed_historical_oracle_not_rebound'
        except Exception as exc:
            report.update(state='original_source_unavailable', error_type=type(exc).__name__, provider_gets=None)
        save(folder / 'fetch-receipt.json', report)
        return report

    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(collect, descriptors))
    manifest = {'sources':[{'id':r['id'], 'source':str(out/'private'/r['id']/'source.json'),
                            'oracle':str(method/'evidence'/(r['id']+'-oracle.json'))} for r in descriptors]}
    save(out / 'private/manifest.json', manifest)
    receipt = {'schema':'nuluno-public-source-hash-replay-v1', 'method':a.method,
               'registered_denominator':len(descriptors), 'sources':rows,
               'candidate_or_baseline_executed':False, 'external_writes':0,
               'complete_cost':None, 'sales_result':None}
    save(out / 'fetch-receipt.json', receipt)
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__': main()
