#!/usr/bin/env python3
"""Run the unchanged frozen method against the installed Community kernel.

This host layout wrapper never edits frozen method files or Engine modules. It
creates only ignored local symlinks and copies, verifies the kernel hashes, and
uses the selected interpreter. Check-only mode does not evaluate any source.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
METHOD_FILES = ('candidate.py', 'demo.py', 'engine_demo.py', 'programs.json',
                'candidate-freeze.json', 'engine-method-freeze.json',
                'run_registered.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError('keep_original_receipt_use_new_output')
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--python', required=True)
    p.add_argument('--kernel-root', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--check-only', action='store_true')
    p.add_argument('--registration')
    p.add_argument('--manifest')
    a = p.parse_args()
    interpreter = Path(a.python).absolute()
    kernel = Path(a.kernel_root).resolve()
    out = Path(a.out).resolve()
    if kernel.name != 'growth_loop' or kernel.parent.name != 'nuluno_engine_community':
        raise ValueError('installed_community_namespace_required_not_legacy_growth_loop')
    if not interpreter.is_file() or not (kernel / '__init__.py').is_file():
        raise ValueError('installed_community_interpreter_and_package_required')
    if out.exists():
        raise ValueError('keep_original_receipts_use_new_output')
    frozen = json.loads((HERE / 'engine-method-freeze.json').read_text())
    for name, expected in frozen['files'].items():
        if sha((HERE / name).read_bytes()) != expected:
            raise ValueError('frozen_method_changed_' + name)
    kernels = {
        'artifact_kernel.py': frozen['engine_artifact_kernel_sha256'],
        'artifact_evolution.py': frozen['engine_artifact_evolution_sha256'],
        'public_sources.py': json.loads((HERE / 'candidate-freeze.json').read_text())['baseline_sha256'],
    }
    for name, expected in kernels.items():
        if sha((kernel / name).read_bytes()) != expected:
            raise ValueError('community_kernel_differs_from_method_freeze_' + name)
    layout = out / 'private/layout'
    method = layout / 'learning'
    method.mkdir(parents=True)
    for name in METHOD_FILES:
        shutil.copyfile(HERE / name, method / name)
    runtime = layout / 'engine/nuluno-engine-0.12.0/skills/growth-meta-ig-loop/runtime'
    runtime.mkdir(parents=True)
    (runtime / 'growth_loop').symlink_to(kernel, target_is_directory=True)
    probe_code = (
        'import sys,json,importlib.metadata as md;from pathlib import Path;'
        'import nuluno_engine_community.growth_loop.artifact_kernel as installed;'
        'sys.path.insert(0,sys.argv[1]);import engine_demo as d;'
        'd.checked(sys.argv[2]);d.check_freeze(Path(sys.argv[1])/"candidate-freeze.json");'
        'import growth_loop.artifact_kernel as used;'
        'assert Path(used.__file__).resolve()==Path(installed.__file__).resolve();'
        'print(json.dumps({"distribution":"nuluno-engine-community",'
        '"version":md.version("nuluno-engine-community"),'
        '"installed_namespace_verified":True,"frozen_method_check":True}))'
    )
    env = {'PATH': os.defpath, 'PYTHONHASHSEED': '0'}
    probe = subprocess.run([str(interpreter), '-I', '-c', probe_code, str(method),
                            str(method / 'engine-method-freeze.json')],
                           cwd=str(HERE), env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=15)
    if probe.returncode:
        # Keep interpreter paths/errors in ignored private evidence only.
        (out / 'private/probe-error.txt').write_bytes(probe.stderr)
        raise ValueError('installed_community_probe_failed_see_private_error')
    record = json.loads(probe.stdout)
    record.update(schema='nuluno-community-method-wrapper-v1',
                  method_freeze_sha256=sha((HERE / 'engine-method-freeze.json').read_bytes()),
                  wrapper_sha256=sha(Path(__file__).read_bytes()),
                  kernel_sha256=kernels, check_only=a.check_only,
                  candidate_executed=False, baseline_executed=False,
                  new_network_requests=0, external_writes=0,
                  complete_cost=None, sales_result=None)
    if not a.check_only:
        if not a.registration or not a.manifest:
            raise ValueError('complete_registration_and_independent_manifest_required')
        launch = ('import sys,runpy;sys.path.insert(0,sys.argv[1]);'
                  'sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name="__main__")')
        run = subprocess.run([str(interpreter), '-I', '-c', launch, str(method), str(method / 'run_registered.py'),
                              '--registration', str(Path(a.registration).resolve()),
                              '--manifest', str(Path(a.manifest).resolve()),
                              '--freeze', str(method / 'engine-method-freeze.json'),
                              '--out', str(out / 'evaluation')],
                             cwd=str(HERE), env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=60)
        if run.returncode:
            (out / 'private/run-error.txt').write_bytes(run.stderr)
            raise ValueError('community_evaluation_failed_see_private_error')
        record['evaluation_receipt_sha256'] = sha((out / 'evaluation/engine-observation.json').read_bytes())
        result = json.loads((out / 'evaluation/engine-observation.json').read_text())
        record['evaluation_state'] = result['state']
        record['candidate_executed'] = bool(result.get('independent_claim_checks'))
        record['baseline_executed'] = bool(result.get('independent_claim_checks'))
    write_new(out / 'community-wrapper-receipt.json', record)
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
