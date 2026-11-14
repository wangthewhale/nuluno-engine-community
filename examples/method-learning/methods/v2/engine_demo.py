#!/usr/bin/env python3
"""Use the unchanged Engine 0.12 artifact lifecycle for a frozen method.

The host selector is fixed reviewed code. Candidate artifacts are bounded JSON
programs, never native code sent for execution. Independent claim coverage must
pass before the host gives an expected output to Engine's exact-output suite.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / 'engine/nuluno-engine-0.12.0'
RUNTIME = ENGINE / 'skills/growth-meta-ig-loop/runtime'
sys.path.insert(0, str(RUNTIME))
from growth_loop.artifact_kernel import make_artifact, validate_program, digest
from growth_loop.artifact_evolution import ArtifactEvolution
from growth_loop.store import Store
from demo import canonical, sha, save, check_freeze, normalize, load_module

RUN = 'public-method-demonstration'
BRAND = 'https://demo.nuluno.example'
METHOD_FILES = ['candidate.py', 'demo.py', 'engine_demo.py', 'programs.json']


def freeze(path):
    check_freeze(HERE / 'candidate-freeze.json')
    programs = json.loads((HERE / 'programs.json').read_text())
    for name in ['baseline', 'candidate', 'rejected_proposal']: validate_program(programs[name])
    result = {'schema': 'nuluno-engine-method-freeze-v1', 'created_at': time.time(),
              'files': {name: sha((HERE / name).read_bytes()) for name in METHOD_FILES},
              'engine_artifact_kernel_sha256': sha((RUNTIME / 'growth_loop/artifact_kernel.py').read_bytes()),
              'engine_artifact_evolution_sha256': sha((RUNTIME / 'growth_loop/artifact_evolution.py').read_bytes()),
              'baseline_program_digest': digest(programs['baseline']), 'candidate_program_digest': digest(programs['candidate']),
              'training_urls': ['https://plausible.io/', 'https://usefathom.com/pricing', 'https://simpleanalytics.com/pricing', 'https://www.simpleanalytics.com/pricing', 'https://buttondown.com/pricing'], 'test_urls': 'not revealed before this freeze',
              'host_pure_tool': 'candidate.py fixed verbatim context selector; no new model/connector authority',
              'maximum_external_writes': 0, 'maximum_view_bytes': 2500,
              'minimum_independent_sources': 3,
              'native_validation_expected_output_policy': 'Use candidate exact output only after every independent original-source claim set is complete, source spans exact, budget met, and no source has less coverage than baseline. At least one source must strictly improve. Any failed source prevents adoption; no source may be dropped.',
              'actual_model_call_in_runtime': False, 'proposal_author': 'AI coding agent in this development chat',
              'model_weights_updated': False, 'economic_adoption_proven': False,
              'rollback_demonstration': 'Explicit controlled missing-block input simulates host parser failure; Engine auto-rolls back on actual pure-program exception. Not a naturally observed external regression.'}
    save(path, result)
    print(json.dumps({'freeze': Path(path).name, 'freeze_sha256': sha(Path(path).read_bytes()), 'candidate_program_digest': result['candidate_program_digest']}))


def checked(path):
    value = json.loads(Path(path).read_text())
    for name, expected in value['files'].items():
        if sha((HERE / name).read_bytes()) != expected: raise ValueError('frozen_method_file_changed_' + name)
    for key, name in [('engine_artifact_kernel_sha256', 'artifact_kernel.py'), ('engine_artifact_evolution_sha256', 'artifact_evolution.py')]:
        if sha((RUNTIME / 'growth_loop' / name).read_bytes()) != value[key]: raise ValueError('frozen_original_engine_changed')
    return value


def prepare_cases(manifest, frozen):
    sources = manifest['sources']
    if len(sources) < frozen['minimum_independent_sources'] or len({row['id'] for row in sources}) != len(sources):
        raise ValueError('complete_independent_source_denominator_required')
    method = load_module(HERE / 'candidate.py', 'frozen_selector')
    baseline = load_module(RUNTIME / 'growth_loop/public_sources.py', 'original_compactor')
    cases, evidence, urls, hashes = [], [], set(), set()
    for row in sources:
        source = json.loads(Path(row['source']).read_text()); oracle = json.loads(Path(row['oracle']).read_text())
        if source['url'] in frozen['training_urls']: raise ValueError('heldout_overlaps_training')
        if source['url'] in urls or source['body_sha256'] in hashes: raise ValueError('independent_sources_must_be_distinct')
        urls.add(source['url']); hashes.add(source['body_sha256'])
        if oracle.get('independent_from_candidate') is not True or oracle.get('source_body_sha256') != source['body_sha256']:
            raise ValueError('independent_source_bound_oracle_required')
        claims = oracle['claims']
        if not claims or len({c['id'] for c in claims}) != len(claims): raise ValueError('unique_complete_claim_denominator_required')
        old = baseline.compact_public_source(source, 2500)
        new = method.select(source, 2500)
        spans = new['source_spans']
        arguments = {'prefix_text': old['text'], 'blocks': [{'selected': True, 'text': source['text'][a:b]} for a,b in spans]}
        outputs = {'baseline': old['text'], 'candidate': new['view']['text']}
        rows = []
        for claim in claims:
            quote = normalize(claim['quote'])
            if not quote or quote not in normalize(source['text']): raise ValueError('oracle_quote_missing_from_original_source')
            rows.append({'id': claim['id'], 'quote_sha256': sha(quote.encode()),
                         'baseline': quote in normalize(outputs['baseline']), 'candidate': quote in normalize(outputs['candidate'])})
        b, c = sum(r['baseline'] for r in rows), sum(r['candidate'] for r in rows)
        span_pass = outputs['candidate'] == '\n[…]\n'.join(source['text'][a:b] for a,b in spans)
        passed = c == len(rows) and c >= b and new['view_bytes'] <= 2500 and span_pass
        evidence.append({'id': row['id'], 'url': source['url'], 'source_body_sha256': source['body_sha256'],
                         'oracle_sha256': sha(Path(row['oracle']).read_bytes()), 'claims': rows,
                         'baseline_coverage': b, 'candidate_coverage': c, 'claim_denominator': len(rows),
                         'source_span_integrity': span_pass, 'candidate_view_bytes': new['view_bytes'], 'objective_gate_pass': passed})
        # Never hide a failing source by omitting it from the denominator.
        cases.append({'id': row['id'], 'input': arguments, 'expected': {'text': outputs['candidate']}})
    return cases, evidence


def run(args):
    frozen = checked(args.freeze)
    manifest = json.loads(Path(args.manifest).read_text())
    cases, evidence = prepare_cases(manifest, frozen)
    report = {'schema': 'nuluno-engine-method-demo-v1', 'method_freeze_sha256': sha(Path(args.freeze).read_bytes()),
              'source_manifest_sha256': sha(Path(args.manifest).read_bytes()), 'source_denominator': len(cases),
              'independent_claim_checks': evidence, 'economic_adoption_proven': False, 'sales_result': None,
              'model_cost': None, 'complete_cost': None, 'model_weights_updated': False,
              'runtime_model_calls': 0, 'external_writes': 0,
              'scope': 'Original Engine bounded artifact lifecycle with a newly reviewed fixed host selector; not a general sales-performance trial.'}
    if not all(row['objective_gate_pass'] for row in evidence) or not any(row['candidate_coverage'] > row['baseline_coverage'] for row in evidence):
        report.update(state='rejected_before_native_adoption', reason='source_coverage_gate_failed_or_no_strict_improvement', original_head_unchanged=True)
        save(Path(args.out) / 'engine-observation.json', report); print(json.dumps(report)); return
    contract = {'scope': {'tenant_id': 'public-demo', 'brand_id': BRAND, 'product_id': 'public-demo'},
                'context': {'model': 'ai-authored-candidate-no-runtime-model-call',
                            'initial_tools_digest': sha((HERE / 'candidate.py').read_bytes()),
                            'permission_digest': digest({'public_get': True, 'external_writes': 0}),
                            'budget_digest': digest({'view_bytes': 2500, 'model_calls': 0}),
                            'outcome_definition': 'complete_source_backed_claim_coverage_within_same_view_budget'},
                'source_refs': ['public:original-prefix-failure']}
    programs = json.loads((HERE / 'programs.json').read_text())
    original = make_artifact('program', programs['baseline'], contract, None, 'Original prefix-only selection', contract['source_refs'])
    suite = {'scope': contract['scope'], 'source_ref': 'host:original-live-source-claims', 'independent': True,
             'hidden_from_model': True, 'fixture': False, 'minimum_cases': 3,
             'cases': cases, 'case_ids': [c['id'] for c in cases], 'training_case_ids': ['training-plausible']}
    settings = {'enabled': True, 'contracts': {RUN: contract}, 'baselines': {RUN: {original['id']: original}},
                'original_heads': {RUN: {'program': original['id']}}, 'validation_suites': {'heldout': suite},
                'rollback_on_failure': True}
    config = {'allow_evolution': True, 'runs': [RUN], 'artifact_evolution': settings}
    workspace = Path(args.out) / 'private/engine-state'
    if workspace.exists(): raise ValueError('keep_original_demo_workspace_use_new_out_for_new_run')
    store = Store(str(workspace)); store.create_run({'brand': {'url': BRAND}, 'product_id': 'public-demo', 'mode': 'prepare'}, RUN)
    engine = ArtifactEvolution(store, config)
    proposal = engine.propose(RUN, {'kind': 'program', 'body': programs['candidate'],
                                   'hypothesis': 'Select source-backed commercial context instead of prefix', 'source_refs': contract['source_refs']})
    artifact = proposal['artifact']; validation = engine.validate(RUN, artifact['id'], 'heldout')
    adoption = engine.adopt(RUN, artifact['id'], validation['id'])
    first = engine.execute(RUN, artifact['id'], cases[0]['input'])
    # Close the first Engine instance, then read and execute persisted state in
    # a fresh isolated Python process. The harness itself only orchestrates.
    config_path = Path(args.out) / 'private/resume-config.json'
    input_path = Path(args.out) / 'private/resume-input.json'
    save(config_path, config); save(input_path, cases[0]['input'])
    store.close()
    resume_code = ('import sys,json,os;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                   'import engine_demo as d;s=d.Store(sys.argv[2]);'
                   'e=d.ArtifactEvolution(s,json.loads(Path(sys.argv[3]).read_text()));'
                   'v=e.execute(sys.argv[4],sys.argv[5],json.loads(Path(sys.argv[6]).read_text()));'
                   'print(json.dumps({"result":v,"pid":os.getpid(),"head":e.head(sys.argv[4],"program")}));s.close()')
    fresh = subprocess.run([sys.executable, '-I', '-S', '-c', resume_code, str(HERE), str(workspace.resolve()),
                            str(config_path.resolve()), RUN, artifact['id'], str(input_path.resolve())],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                           env={'PATH': os.defpath, 'PYTHONHASHSEED': '0'})
    if fresh.returncode or len(fresh.stdout) > 1024 * 1024: raise ValueError('fresh_process_resume_failed')
    resumed_record = json.loads(fresh.stdout); resumed = resumed_record['result']
    store = Store(str(workspace)); engine = ArtifactEvolution(store, config)
    report.update(state='adopted_execution', original_artifact_id=original['id'], candidate_artifact_id=artifact['id'],
                  native_validation_receipt_id=validation['id'], native_all_cases_pass=validation['all_cases_pass'],
                  native_execution_improvement_verified=validation['execution_improvement_verified'],
                  adoption=adoption['state'], restart_same_output=digest(first)==digest(resumed),
                  restart_process_changed=resumed_record['pid'] != os.getpid(),
                  restart_read_original_head=resumed_record['head']['id'] == artifact['id'],
                  restart_scope='fresh_python_process_original_sqlite_and_saved_host_config')
    # A more expensive proposal retaining the failed prefix is rejected by the
    # same native suite. It is an actual rejected candidate, not a fake pass.
    bad = engine.propose(RUN, {'kind': 'program', 'body': programs['rejected_proposal'],
                              'hypothesis': 'Try an extra identity step with original prefix; verify before adoption', 'source_refs': contract['source_refs']})['artifact']
    bad_validation = engine.validate(RUN, bad['id'], 'heldout')
    try:
        engine.adopt(RUN, bad['id'], bad_validation['id']); report['failed_proposal_adoption_rejected'] = False
    except ValueError:
        report['failed_proposal_adoption_rejected'] = True
    # Explicitly labelled failure injection tests the real rollback path.
    broken = copy.deepcopy(cases[0]['input']); broken.pop('blocks')
    try:
        engine.execute(RUN, artifact['id'], broken); report['fault_injection_raised'] = False
    except ValueError:
        report['fault_injection_raised'] = True
    report['final_native_head'] = engine.head(RUN, 'program')['id']
    report['automatic_rollback_head_is_original'] = report['final_native_head'] == original['id']
    report['rollback_evidence_class'] = 'controlled_missing_block_fault_after_real_source_validation_not_natural_external_regression'
    report['state'] = 'native_lifecycle_completed_after_controlled_rollback' if report['automatic_rollback_head_is_original'] else 'rollback_not_verified'
    store.close()
    save(Path(args.out) / 'engine-observation.json', report)
    print(json.dumps(report, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('freeze'); p.add_argument('--out', required=True)
    p = sub.add_parser('run'); p.add_argument('--freeze', required=True); p.add_argument('--manifest', required=True); p.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.command == 'freeze': freeze(args.out)
    else: run(args)


if __name__ == '__main__': main()
