"""Operator-installed DATA libraries. No package code/configuration is executed.

Archive hashes must come from the operator's trusted delivery record. This is
integrity checking, not a claim that a manifest proves payment or legal rights.
The pack directory is independent of all run state and platform authority.
"""
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA = 'nuluno-method-pack-v1'
PACK_ID = 'nuluno-sales-methods'
KERNEL_CONTRACT = 'nuluno-method-resources-v1'
LIMIT = 64 * 1024 * 1024
MAIN = 'skills/growth-meta-ig-loop/references/'


def _json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _path(name):
    if not isinstance(name, str) or len(name) > 240 or '\\' in name:
        raise ValueError('pack_unsafe_path')
    parts = PurePosixPath(name).parts
    if not parts or name.startswith('/') or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) or part in ('.', '..') for part in parts):
        raise ValueError('pack_unsafe_path')
    if any(part in {'credentials.json','authorization.json','service.json','.env','runtime','agents','private'} for part in parts):
        raise ValueError('pack_code_or_authority_file_forbidden')
    if name in ('pack.json', 'LICENSE.txt', 'library/skill-catalog.json', 'library/workflow-catalog.json'):
        return name
    if len(parts) < 4 or parts[:2] != ('library', 'skills') or not re.fullmatch(r'growth-[a-z0-9-]+', parts[2]):
        raise ValueError('pack_code_or_authority_file_forbidden')
    tail = parts[3:]
    if tail in (('SKILL.md',), ('.growth-pack.json',)):
        return name
    if tail[0] == 'references' and len(tail) >= 2 and Path(name).suffix in ('.json', '.md', '.txt'):
        return name
    raise ValueError('pack_code_or_authority_file_forbidden')


def _unique(rows, key='id'):
    if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get(key), str) for r in rows):
        raise ValueError('pack_invalid_catalog')
    result = {row[key]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError('pack_duplicate_identity')
    return result


def validate_library(library, starter):
    """Validate complete executable dependency closure, not just stated counts."""
    library, starter = Path(library), Path(starter)
    ref = library / MAIN
    blueprint = _json(ref / 'blueprint.json')
    common = _unique(blueprint['skills'])
    flows = _unique(blueprint['workflows'])
    channels = _unique(_json(ref / 'channel-catalog.json')['channel_skills'])
    goals = _unique(_json(ref / 'goal-catalog.json')['goals'])
    from .contracts import FIELDS
    if set(common) != set(FIELDS) or set(common) & set(channels):
        raise ValueError('pack_invalid_skill_identity')
    original_s01 = _unique(_json(starter / MAIN / 'blueprint.json')['skills'])['S01']
    if common['S01'] != original_s01:
        raise ValueError('pack_starter_contract_changed')
    for rel in ('references/contract.json', 'references/procedure.md', 'SKILL.md'):
        if (library / 'skills/growth-read-existing-offer' / rel).read_bytes() != (starter / 'skills/growth-read-existing-offer' / rel).read_bytes():
            raise ValueError('pack_starter_contract_changed')
    known = set(common) | set(channels)
    for row in channels.values():
        if row.get('parent_skill_id') not in common:
            raise ValueError('pack_unknown_parent_skill')
    for flow in flows.values():
        if not flow.get('stages') or any(not set(stage.get('skill_ids', [])) <= known for stage in flow['stages']):
            raise ValueError('pack_unknown_workflow_dependency')
    for goal in goals.values():
        if not set(goal.get('skills', [])) <= known or not set(goal.get('bottleneck_routes', {}).values()) <= known:
            raise ValueError('pack_unknown_goal_dependency')
    graph_paths = list((ref / 'graphs').glob('*.json'))
    graphs = _unique([_json(p) for p in graph_paths])
    if any(_json(p)['id'] != p.stem for p in graph_paths): raise ValueError('pack_graph_filename_identity_mismatch')
    if set(graphs) != set(flows) | set(goals):
        raise ValueError('pack_incomplete_graph_catalog')
    terminals = {'branch_observation', 'flow.finalize', 'reconcile_same_operation', 'wait_owner', 'wait_until_reset'}
    for ident, graph in graphs.items():
        if graph.get('schema') not in ('growth-graph-v1', 'growth-goal-graph-v1'):
            raise ValueError('pack_unknown_graph_schema')
        nodes = _unique(graph.get('nodes', []))
        if not nodes:
            raise ValueError('pack_empty_graph')
        for node in nodes.values():
            skill = node.get('skill_id', node.get('skill'))
            if skill is not None and skill not in known:
                raise ValueError('pack_unknown_graph_dependency')
            if not set(node.get('depends', [])) <= set(nodes):
                raise ValueError('pack_unknown_graph_dependency')
        visiting, visited = set(), set()
        def visit(node):
            if node in visiting: raise ValueError('pack_graph_dependency_cycle')
            if node in visited: return
            visiting.add(node)
            for dependency in nodes[node].get('depends', []): visit(dependency)
            visiting.remove(node); visited.add(node)
        for node in nodes: visit(node)
        for edge in graph.get('edges', []):
            if set(edge) != {'from', 'to', 'when'} or edge['from'] not in nodes or edge['to'] not in set(nodes) | terminals:
                raise ValueError('pack_unknown_graph_dependency')
        if ident in flows:
            from .graph import compile_graph
            if graph != compile_graph(flows[ident]):
                raise ValueError('pack_method_graph_contract_mismatch')
    folders = [p for p in (library / 'skills').iterdir() if p.is_dir() and (p / 'SKILL.md').is_file() and (p / '.growth-pack.json').is_file()]
    names = {p.name for p in folders}
    catalog = _json(library / 'skill-catalog.json')
    work = _unique(catalog['work_skills'])
    if set(work) != set(common) or any(row['name'] not in names for row in list(work.values()) + list(channels.values())):
        raise ValueError('pack_incomplete_skill_folders')
    for name in names - {'growth-meta-ig-loop'}:
        if not (library / 'skills' / name / 'references/contract.json').is_file() or not (library / 'skills' / name / 'references/procedure.md').is_file():
            # The composed S35 method is a real recipe with references, rather
            # than another work skill; its full procedure is its SKILL.md.
            if name != 'growth-find-raised-hand-buyers':
                raise ValueError('pack_incomplete_skill_contract')
    execution = _json(ref / 'execution-catalog.json')
    if set(_unique(execution['work'])) != known or set(_unique(execution['goals'])) != set(goals):
        raise ValueError('pack_incomplete_execution_catalog')
    # Only the trusted installed compiler is run. The library contributes JSON,
    # never a Python import path or executable file. A separate process avoids
    # mutating another worker's resource globals while checking a candidate.
    runtime = Path(__file__).resolve().parents[1]
    code = '''import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import growth_loop.resources as r
root=Path(sys.argv[2])/'skills/growth-meta-ig-loop'
r.skill_root=lambda:root
r.reference=lambda name:root/'references'/name
from growth_loop.goals import compile_goal
ref=root/'references'
for goal in json.loads((ref/'goal-catalog.json').read_text())['goals']:
 expected=compile_goal(goal)
 actual=json.loads((ref/'graphs'/(goal['id']+'.json')).read_text())
 if expected!=actual:raise ValueError('goal_graph_contract_mismatch')
'''
    try:
        result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(runtime),str(library)],
                              env={'PATH':os.defpath},cwd=str(runtime),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
    except subprocess.TimeoutExpired: raise ValueError('pack_graph_compilation_limit')
    if result.returncode: raise ValueError('pack_goal_graph_contract_mismatch')
    return {'work_skills': len(known), 'skill_folders': len(folders), 'goals': len(goals), 'graph_templates': len(graphs), 'channel_skills': len(channels)}


def validate_directory(directory, starter):
    directory = Path(directory)
    manifest = _json(directory / 'pack.json')
    if set(manifest) != {'schema', 'pack_id', 'pack_version', 'kind', 'requires', 'files', 'source_archive_sha256', 'license'}:
        raise ValueError('pack_unknown_manifest_fields')
    if manifest['schema'] != SCHEMA or manifest['pack_id'] != PACK_ID or manifest['kind'] != 'commercial_data_only':
        raise ValueError('pack_schema_or_identity_rejected')
    if not isinstance(manifest['pack_version'],str) or not re.fullmatch(r'\d+\.\d+\.\d+', manifest['pack_version']): raise ValueError('pack_invalid_version')
    if manifest['requires'] != {'kernel_contract': KERNEL_CONTRACT, 'starter_skill_id': 'S01'}:
        raise ValueError('pack_kernel_dependency_rejected')
    if manifest['license'] != 'commercial-end-user' or not re.fullmatch(r'[0-9a-f]{64}', manifest['source_archive_sha256']):
        raise ValueError('pack_license_or_source_rejected')
    files = manifest['files']
    if not isinstance(files, dict) or not files or len(files) > 6000: raise ValueError('pack_invalid_file_table')
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    if actual != set(files) | {'pack.json'}: raise ValueError('pack_unlisted_files')
    for name, entry in files.items():
        _path(name)
        if set(entry) != {'sha256', 'bytes'} or type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= 8 * 1024 * 1024:
            raise ValueError('pack_invalid_file_entry')
        path = directory / name
        if path.is_symlink() or not path.is_file(): raise ValueError('pack_link_rejected')
        data = path.read_bytes()
        if len(data) != entry['bytes'] or _hash(data) != entry['sha256']: raise ValueError('pack_file_hash_mismatch')
    if any(p.is_symlink() for p in directory.rglob('*')): raise ValueError('pack_link_rejected')
    if sum(v['bytes'] for v in files.values()) > LIMIT: raise ValueError('pack_expansion_limit')
    return manifest, validate_library(directory / 'library', starter)


def active_library(starter, directory=None):
    directory = directory or os.environ.get('NULUNO_METHOD_PACK_DIR')
    if not directory: return Path(starter), None
    root = Path(directory).resolve()
    pointer = root / 'active.json'
    if not pointer.exists(): return Path(starter), None
    if pointer.is_symlink(): raise ValueError('pack_pointer_link_rejected')
    value = _json(pointer)
    if set(value) != {'schema', 'archive_sha256', 'manifest_sha256'} or value['schema'] != SCHEMA:
        raise ValueError('pack_invalid_active_pointer')
    if not all(re.fullmatch(r'[0-9a-f]{64}', value[k]) for k in ('archive_sha256', 'manifest_sha256')):
        raise ValueError('pack_invalid_active_pointer')
    release = root / 'releases' / value['archive_sha256']
    if release.is_symlink() or release.resolve() != release: raise ValueError('pack_release_link_rejected')
    if _hash((release / 'pack.json').read_bytes()) != value['manifest_sha256']: raise ValueError('pack_manifest_changed')
    manifest, counts = validate_directory(release, starter)
    return release / 'library', {'pack_id': manifest['pack_id'], 'pack_version': manifest['pack_version'], 'archive_sha256': value['archive_sha256'], 'counts': counts, 'license': manifest['license']}


def install_pack(archive, directory, expected_sha256, starter):
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or ''): raise ValueError('operator_archive_sha256_required')
    if Path(archive).stat().st_size > LIMIT: raise ValueError('pack_archive_size_limit')
    data = Path(archive).read_bytes()
    if len(data) > LIMIT or _hash(data) != expected_sha256: raise ValueError('pack_archive_hash_mismatch')
    root = Path(directory).absolute()
    if any(p.is_symlink() for p in [root, *root.parents]): raise ValueError('pack_install_path_link_rejected')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = root / '.install.lock'
    try: fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError: raise ValueError('pack_install_in_progress_or_stale_lock_requires_operator_review')
    os.close(fd)
    try: stage = Path(tempfile.mkdtemp(prefix='.install-', dir=root))
    except BaseException:
        lock.unlink(); raise
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zipped:
            infos = zipped.infolist()
            if len(infos) > 6001 or len({i.filename for i in infos}) != len(infos): raise ValueError('pack_duplicate_or_excess_entries')
            total = 0
            for info in infos:
                _path(info.filename)
                if info.is_dir() or stat.S_IFMT(info.external_attr >> 16) not in (0,stat.S_IFREG) or info.flag_bits & 1: raise ValueError('pack_archive_entry_rejected')
                total += info.file_size
                if info.file_size > 8 * 1024 * 1024 or total > LIMIT: raise ValueError('pack_expansion_limit')
                destination = stage / info.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as out: out.write(zipped.read(info))
        manifest, counts = validate_directory(stage, starter)
        old_library, current = active_library(starter, root)
        if current and tuple(map(int, manifest['pack_version'].split('.'))) < tuple(map(int, current['pack_version'].split('.'))):
            raise ValueError('pack_downgrade_requires_explicit_review')
        releases = root / 'releases'; releases.mkdir(exist_ok=True)
        if releases.is_symlink(): raise ValueError('pack_release_link_rejected')
        target = releases / expected_sha256
        if target.exists():
            validate_directory(target, starter)
            if _hash((target / 'pack.json').read_bytes()) != _hash((stage / 'pack.json').read_bytes()): raise ValueError('pack_release_identity_changed')
        else: os.rename(stage, target)
        pointer = {'schema': SCHEMA, 'archive_sha256': expected_sha256, 'manifest_sha256': _hash((target / 'pack.json').read_bytes())}
        fd, pending = tempfile.mkstemp(prefix='.active-', dir=root)
        try:
            with os.fdopen(fd, 'w') as out: json.dump(pointer, out); out.flush(); os.fsync(out.fileno())
            os.chmod(pending, 0o600); os.replace(pending, root / 'active.json')
        finally:
            if Path(pending).exists(): Path(pending).unlink()
        return {'installed': True, 'pack_id': manifest['pack_id'], 'pack_version': manifest['pack_version'], 'archive_sha256': expected_sha256,
                'counts': counts, 'runtime_code_executed': False, 'authority_modified': False, 'run_state_modified': False,
                'license': manifest['license'], 'license_payment_independently_verified': False}
    finally:
        if stage.exists(): shutil.rmtree(stage)
        lock.unlink()
