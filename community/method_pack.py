#!/usr/bin/env python3
"""Inspect portable method proposals. Never execute code, grant access or adopt."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

MAX_BYTES = 2 * 1024 * 1024
SCHEMA = 'nuluno-method-proposal-v1'
NAME = re.compile(r'^[a-z][a-z0-9-]{2,79}$')
DIGEST = re.compile(r'^[a-f0-9]{64}$')
SECRET = re.compile(r'(?i)(?:sk_live_|rk_live_|gh[pousr]_|-----BEGIN [A-Z ]*PRIVATE KEY|Bearer\s+[A-Za-z0-9_-]{20,})')

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

def load_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate_json_key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('finite_json_required')))

def checked_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('relative_file_required')
    parts = Path(name)
    if parts.is_absolute() or '..' in parts.parts:
        raise ValueError('unsafe_path')
    target = root / parts
    if any((root / Path(*parts.parts[:i])).is_symlink() for i in range(1, len(parts.parts) + 1)):
        raise ValueError('symlink_not_allowed')
    if not target.resolve().is_relative_to(root) or not target.is_file():
        raise ValueError('missing_or_outside_file')
    if target.stat().st_size > MAX_BYTES:
        raise ValueError('file_too_large')
    return target.read_bytes()

def inspect(root, with_kernel=False):
    root = Path(root).resolve()
    manifest = load_json(checked_file(root, 'method.json'))
    fields = {'schema', 'method_id', 'version', 'parent_digest', 'summary', 'applicability',
              'proposal', 'files', 'license', 'evidence', 'requested_permissions'}
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest['schema'] != SCHEMA:
        raise ValueError('method_manifest_invalid')
    if not isinstance(manifest['method_id'], str) or not NAME.fullmatch(manifest['method_id']):
        raise ValueError('method_id_invalid')
    if not isinstance(manifest['version'], str) or not re.fullmatch(r'\d+\.\d+\.\d+', manifest['version']):
        raise ValueError('method_version_invalid')
    if manifest['parent_digest'] is not None and not DIGEST.fullmatch(str(manifest['parent_digest'])):
        raise ValueError('parent_digest_invalid')
    if not isinstance(manifest['summary'], str) or not 1 <= len(manifest['summary']) <= 1000:
        raise ValueError('summary_required')
    if not isinstance(manifest['applicability'], dict) or set(manifest['applicability']) != {'task', 'conditions', 'limits'}:
        raise ValueError('applicability_required')
    if not all(isinstance(manifest['applicability'][k], str) and manifest['applicability'][k]
               for k in ('task', 'conditions', 'limits')):
        raise ValueError('applicability_text_required')
    # A shared proposal is data, not an identity, credential or platform grant.
    if manifest['requested_permissions'] != []:
        raise ValueError('permissions_require_separate_host_review')
    proposal = manifest['proposal']
    if not isinstance(proposal, dict) or set(proposal) != {'kind', 'path'} or proposal['kind'] != 'program':
        raise ValueError('pure_program_proposal_required')
    files = manifest['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= 20 or 'method.json' in files:
        raise ValueError('file_inventory_invalid')
    listed = {}
    total = 0
    for name, expected in files.items():
        if not isinstance(expected, str) or not DIGEST.fullmatch(expected):
            raise ValueError('sha256_required')
        raw = checked_file(root, name)
        total += len(raw)
        if total > MAX_BYTES or digest(raw) != expected:
            raise ValueError('content_integrity_failed')
        if SECRET.search(raw.decode('utf-8', errors='replace')):
            raise ValueError('possible_secret_do_not_publish')
        listed[name] = raw
    entries = list(root.rglob('*'))
    if any(p.is_symlink() for p in entries):
        raise ValueError('symlink_not_allowed')
    actual = {str(p.relative_to(root)) for p in entries if p.is_file()}
    if actual != set(files) | {'method.json'}:
        raise ValueError('unlisted_file')
    license_info = manifest['license']
    if not isinstance(license_info, dict) or set(license_info) != {'identifier', 'path'}:
        raise ValueError('license_required')
    if not isinstance(license_info['identifier'], str) or not license_info['identifier'] or not isinstance(license_info['path'], str) or license_info['path'] not in listed:
        raise ValueError('license_text_required')
    if not isinstance(proposal['path'], str) or proposal['path'] not in listed:
        raise ValueError('proposal_file_not_listed')
    evidence = manifest['evidence']
    if not isinstance(evidence, dict) or set(evidence) != {'path', 'class', 'data_rights'}:
        raise ValueError('evidence_class_required')
    if not isinstance(evidence['path'], str) or evidence['path'] not in listed or evidence['class'] not in ('illustrative', 'controlled', 'live_technical', 'commercial_comparison'):
        raise ValueError('evidence_not_listed')
    if evidence['data_rights'] not in ('author_owned', 'public_source_permitted', 'explicit_permission'):
        raise ValueError('source_rights_declaration_required')
    # Integrity does not authenticate a contributor's evidence or legal rights.
    body = load_json(listed[proposal['path']])
    kernel_state = 'not_checked'
    if with_kernel:
        try:
            from nuluno_engine_community.growth_loop.artifact_kernel import validate_program
        except ImportError:
            from growth_loop.artifact_kernel import validate_program
        validate_program(body)
        kernel_state = 'bounded_program_valid'
    return {'schema': SCHEMA, 'method_id': manifest['method_id'], 'version': manifest['version'],
            'proposal_digest': digest(canonical(body)), 'manifest_digest': digest(canonical(manifest)),
            'integrity': 'valid', 'kernel': kernel_state, 'evidence_class': evidence['class'],
            'evidence_authenticity_verified': False, 'source_rights_verified': False,
            'state': 'proposal', 'external_execution_authorized': False,
            'adopted': False, 'sales_improvement_proven': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--with-engine-kernel', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.directory, args.with_engine_kernel), sort_keys=True))
    except (ValueError, OSError, ImportError, RecursionError) as error:
        print(json.dumps({'state': 'rejected', 'reason': str(error)[:160]}), file=sys.stderr)
        raise SystemExit(1)

if __name__ == '__main__':
    main()
