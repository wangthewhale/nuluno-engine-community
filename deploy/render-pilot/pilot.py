"""Single-owner deployment adapter. No platform writes, models or new runtime.

The unchanged public kernel still owns tasks, receipt deduplication and quotas.
Only host administration calls these functions. None is an MCP/REST tool.
"""
import argparse
import contextlib
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.parse import parse_qsl, urlsplit
import uuid

METHODS = ['status', 'contract', 'campaign_research', 'campaign_build', 'campaign_read', 'campaign_events',
           'delivery_report', 'execution_metrics', 'cost_summary', 'trust_status', 'learning_status']
WORKER = 'pilot-owner'
SECRET_ENV = 'ENGINE_PILOT_BEARER'
CREDENTIAL_QUERY = re.compile(r'token|password|passwd|secret|apikey|session|credential|authorization|verification|signature|jwt|code$|^key$|^auth$|^sig$|^ticket$|^otp$|^cfchltk$')


def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
def sha(value): return hashlib.sha256(value).hexdigest()
def reject(code): raise ValueError(code)


def regular(path, missing=False):
    if path.is_symlink(): reject('pilot_symlink_rejected')
    if not path.exists():
        if missing: return False
        reject('pilot_original_file_missing')
    if not path.is_file(): reject('pilot_regular_file_required')
    return True


def directory(path, create=False):
    if path.is_symlink(): reject('pilot_symlink_rejected')
    if create: path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir(): reject('pilot_directory_required')
    return path


def read(path):
    regular(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd) as stream: return json.load(stream)


def fsync_directory(path):
    directory(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def fsync_file(path):
    regular(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try: os.fsync(fd)
    finally: os.close(fd)


def replace_durable(staged, path):
    regular(staged); regular(path, missing=True)
    os.replace(staged, path)
    fsync_directory(path.parent)


def atomic(path, value):
    directory(path.parent); regular(path, missing=True)
    fd, name = tempfile.mkstemp(prefix='.pilot-write-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(canonical(value) + b'\n'); stream.flush(); os.fsync(stream.fileno())
        replace_durable(Path(name), path)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def lock(root, name, nonblocking=False):
    path = root / name; regular(path, missing=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)); yield fd
    finally: os.close(fd)


def public_url(value, product=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048: reject('pilot_public_url_required')
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or (product and u.query): reject('pilot_public_url_required')
    if any(CREDENTIAL_QUERY.search(re.sub('[^a-z0-9]', '', k.lower())) for k, _ in parse_qsl(u.query, keep_blank_values=True)): reject('pilot_url_credentials_query_rejected')
    return u


def scope(env):
    product = env.get('ENGINE_PRODUCT_URL', ''); host = public_url(product, product=True)
    urls = json.loads(env.get('ENGINE_SOURCE_URLS_JSON') or json.dumps([product]))
    if not isinstance(urls, list) or not 1 <= len(urls) <= 4 or len(set(urls)) != len(urls): reject('pilot_exact_source_urls_required')
    for value in urls:
        u = public_url(value)
        if (u.scheme, u.hostname, u.port or 443) != (host.scheme, host.hostname, host.port or 443): reject('pilot_same_product_origin_required')
    run = env.get('ENGINE_RUN_ID', 'pilot-product')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', run): reject('pilot_run_id_invalid')
    limit = int(env.get('ENGINE_PUBLIC_READ_LIMIT', '12'))
    if not 1 <= limit <= 100: reject('pilot_read_budget_invalid')
    return {'schema': 'nuluno-cloud-pilot-scope-v1', 'product_url': product, 'source_urls': urls, 'read_limit': limit,
            'worker': WORKER, 'run_id': run, 'methods': METHODS, 'spend_minor': 0, 'platform_writes': False, 'model_calls': False}


def secret(env):
    value = env.get(SECRET_ENV, '')
    if not re.fullmatch('[A-Za-z0-9_-]{32,256}', value) or len(set(value)) < 8 or value.startswith(('REPLACE', 'CHANGEME', 'EXAMPLE')): reject('pilot_original_random_secret_required')
    return value


def root_path(env, enforce_mount=True):
    path = Path(env.get('ENGINE_STATE_DIR', '/state')).absolute()
    directory(path)
    mounted = os.path.ismount(path)
    if not mounted and Path('/proc/self/mountinfo').is_file():
        mounted = any(len(row.split()) > 4 and row.split()[4] == str(path) for row in Path('/proc/self/mountinfo').read_text().splitlines())
    if enforce_mount and (str(path) != '/state' or not mounted): reject('pilot_persistent_state_mount_required')
    return path


def expected_config(pinned):
    from nuluno_engine_community.growth_loop.standalone import initialize_service
    with tempfile.TemporaryDirectory(prefix='pilot-blueprint-') as temp:
        result = initialize_service(Path(temp)/'workspace', Path(temp)/'service.json', pinned['product_url'], pinned['run_id'], worker=WORKER, token_env=SECRET_ENV)
        config = read(Path(temp)/'service.json'); profile = result['profile']
    worker = config['workers'][WORKER]
    worker['methods'] = METHODS; worker['allow_run_create'] = False
    worker['commercial']['tenant_id'] = 'pilot-' + sha(canonical(pinned))[:24]
    worker['public_read_policy'] = {'read_limit': pinned['read_limit'], 'compact_bytes': 2500, 'max_response_bytes': 65536}
    worker['response_policy'] = {'content_only': True, 'max_response_bytes': 65536}
    worker['tool_routes']['public-source']['allowed_urls'] = pinned['source_urls']
    return config, profile


def prepare(env, enforce_mount=True):
    root = root_path(env, enforce_mount); pinned = scope(env); fingerprint = sha(secret(env).encode())
    with lock(root, '.admin.lock'):
        owner_path = root/'owner.json'
        if regular(owner_path, missing=True):
            owner = read(owner_path)
            if owner.get('schema') != 'nuluno-cloud-pilot-owner-v1' or not hmac.compare_digest(owner.get('secret_sha256', ''), fingerprint) or owner.get('scope') != pinned: reject('pilot_original_owner_scope_or_secret_changed')
        else:
            if (root/'service.json').exists() or (root/'workspace').exists(): reject('pilot_unknown_existing_state_preserved')
            owner = {'schema': 'nuluno-cloud-pilot-owner-v1', 'secret_sha256': fingerprint, 'scope': pinned, 'phase': 'pending', 'created_at': time.time()}
            atomic(owner_path, owner)
        workspace = directory(root/'workspace', create=True)
        for name in ('workflow.sqlite3', 'workflow.sqlite3-wal', 'workflow.sqlite3-shm', 'initial-profile.json'): regular(workspace/name, missing=True)
        if owner['phase'] == 'complete' and not (workspace/'workflow.sqlite3').exists(): reject('pilot_original_database_missing')
        config, profile = expected_config(pinned); config_path = root/'service.json'
        if regular(config_path, missing=True):
            existing = read(config_path); test = json.loads(json.dumps(existing)); test['workers'][WORKER].pop('revoked', None)
            if test != config: reject('pilot_original_configuration_changed')
            config = existing
        else:
            if owner['phase'] != 'pending': reject('pilot_original_configuration_missing')
            atomic(config_path, config)
        profile_path = workspace/'initial-profile.json'
        if regular(profile_path, missing=True):
            if read(profile_path) != profile: reject('pilot_original_profile_changed')
        else:
            if owner['phase'] != 'pending': reject('pilot_original_profile_missing')
            atomic(profile_path, profile)
        tombstone = root/'revocation.json'
        if regular(tombstone, missing=True) and read(tombstone).get('revoked') is True:
            if config['workers'][WORKER].get('revoked') is not True:
                config['workers'][WORKER]['revoked'] = True; atomic(config_path, config)
        from nuluno_engine_community.growth_loop.store import Store
        from nuluno_engine_community.growth_loop.service import Service
        store = Store(workspace)
        try:
            if owner['phase'] == 'complete' and not store.db.execute('SELECT 1 FROM runs WHERE id=?', (pinned['run_id'],)).fetchone(): reject('pilot_original_run_missing')
            settings = {**config['workers'][WORKER], 'allow_run_create': True, 'methods': METHODS + ['run_create']}
            result = Service(store, WORKER, settings).run('run_create', {'run_id': pinned['run_id'], 'profile': profile})
            if store.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok': reject('pilot_database_integrity_failed')
        finally: store.close()
        if owner['phase'] != 'complete': owner['phase'] = 'complete'; atomic(owner_path, owner)
    return {'run_id': pinned['run_id'], 'already_created': result.get('already_created', False), 'revoked': config['workers'][WORKER].get('revoked') is True,
            'source_url_count': len(pinned['source_urls']), 'read_limit': pinned['read_limit'], 'state_reset': False}


def revoke(root):
    with lock(root, '.admin.lock'):
        config = read(root/'service.json'); owner = read(root/'owner.json')
        atomic(root/'revocation.json', {'revoked': True, 'at': time.time(), 'scope_sha256': sha(canonical(owner['scope']))})
        config['workers'][WORKER]['revoked'] = True; atomic(root/'service.json', config)
    return {'revoked': True, 'run_id': owner['scope']['run_id'], 'state_reset': False, 'new_key_created': False}


def backup(root, keep=3):
    backups = directory(root/'backups', create=True)
    with lock(root, '.admin.lock'):
        with tempfile.TemporaryDirectory(prefix='.backup-', dir=str(backups)) as temp:
            temp = Path(temp); source = root/'workspace/workflow.sqlite3'; regular(source)
            src = sqlite3.connect('file:' + str(source) + '?mode=ro', uri=True, timeout=10); dst = sqlite3.connect(str(temp/'workflow.sqlite3'))
            try:
                src.backup(dst, pages=100, sleep=0.05)
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok': reject('pilot_backup_integrity_failed')
                if dst.execute('PRAGMA journal_mode=DELETE').fetchone()[0].lower() != 'delete': reject('pilot_backup_checkpoint_failed')
            finally: dst.close(); src.close()
            os.chmod(temp/'workflow.sqlite3', 0o600)
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = temp/('workflow.sqlite3'+suffix)
                if regular(sidecar, missing=True): sidecar.unlink()
            names = ['owner.json', 'service.json', 'workspace/initial-profile.json']
            if regular(root/'revocation.json', missing=True): names.append('revocation.json')
            for name in names:
                target = temp/name; directory(target.parent, create=True); atomic(target, read(root/name))
            payload = ['workflow.sqlite3'] + names
            manifest = {'schema': 'nuluno-cloud-pilot-backup-v1', 'created_at': time.time(), 'source_commit': os.environ.get('ENGINE_SOURCE_COMMIT'),
                        'files': {name: sha((temp/name).read_bytes()) for name in payload}, 'contains_plaintext_agent_key': False}
            atomic(temp/'backup-manifest.json', manifest)
            target = backups/('backup-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex + '.tar.gz')
            fd, staged_archive = tempfile.mkstemp(prefix='.archive-', dir=str(backups)); os.close(fd)
            try:
                with tarfile.open(staged_archive, 'w:gz') as archive:
                    for name in sorted(payload + ['backup-manifest.json']): archive.add(temp/name, arcname=name, recursive=False)
                os.chmod(staged_archive, 0o600)
                fsync_file(Path(staged_archive)); replace_durable(Path(staged_archive), target)
            finally:
                if os.path.exists(staged_archive): os.unlink(staged_archive)
    for old in sorted(backups.glob('backup-*.tar.gz'))[:-keep]: regular(old); old.unlink()
    fsync_directory(backups); fsync_directory(root)
    return {'backup': target.name, 'sha256': sha(target.read_bytes()), 'sqlite_online_backup': True, 'raw_wal_copied': False}


def restore(root, env):
    """Offline, explicitly requested recovery. Always leaves old worker revoked.

    Caller must hold .runtime.lock before this function. The independent restore
    journal is never imported from a backup, so repeated boot cannot reset data.
    """
    request_id = env.get('ENGINE_RESTORE_REQUEST_ID', '')
    name = env.get('ENGINE_RESTORE_ARCHIVE', '')
    if not re.fullmatch('[a-f0-9-]{36}', request_id) or not re.fullmatch('backup-[A-Za-z0-9-]+\.tar\.gz', name): reject('pilot_explicit_restore_request_required')
    try:
        if str(uuid.UUID(request_id)) != request_id: reject('pilot_explicit_restore_request_required')
    except ValueError: reject('pilot_explicit_restore_request_required')
    archive_path = root/'backups'/name; regular(archive_path)
    if archive_path.stat().st_size > 128*1024*1024: reject('pilot_backup_byte_limit')
    archive_sha = sha(archive_path.read_bytes()); journal_path = root/'restore-journal.json'
    with lock(root, '.admin.lock'):
        journal = read(journal_path) if regular(journal_path, missing=True) else {'requests': {}}
        previous = journal['requests'].get(request_id)
        if previous:
            if previous['archive_sha256'] != archive_sha: reject('pilot_restore_request_changed')
            if previous['state'] == 'complete': return {'restored': False, 'original_restore_reused': True, 'revoked': True}
        owner = read(root/'owner.json')
        if owner['scope'] != scope(env) or not hmac.compare_digest(owner['secret_sha256'], sha(secret(env).encode())): reject('pilot_restore_original_owner_required')
        with tempfile.TemporaryDirectory(prefix='.restore-', dir=str(root)) as temp:
            temp = Path(temp); allowed = {'backup-manifest.json', 'workflow.sqlite3', 'owner.json', 'service.json', 'workspace/initial-profile.json', 'revocation.json'}
            total = 0; names = set()
            with tarfile.open(archive_path, 'r:gz') as archive:
                for member in archive:
                    if member.name not in allowed or member.name in names or not member.isfile(): reject('pilot_backup_member_rejected')
                    total += member.size
                    if member.size < 0 or total > 128*1024*1024: reject('pilot_backup_byte_limit')
                    names.add(member.name); target = temp/member.name; directory(target.parent, create=True)
                    with open(target, 'xb') as output: shutil.copyfileobj(archive.extractfile(member), output)
                    os.chmod(target, 0o600)
            manifest = read(temp/'backup-manifest.json')
            if manifest.get('schema') != 'nuluno-cloud-pilot-backup-v1' or set(manifest['files']) != names-{'backup-manifest.json'}: reject('pilot_backup_manifest_invalid')
            for member, expected in manifest['files'].items():
                if sha((temp/member).read_bytes()) != expected: reject('pilot_backup_hash_mismatch')
            saved_owner = read(temp/'owner.json')
            if saved_owner['scope'] != owner['scope'] or saved_owner['secret_sha256'] != owner['secret_sha256']: reject('pilot_backup_owner_mismatch')
            db = sqlite3.connect(str(temp/'workflow.sqlite3'))
            try:
                db.execute('PRAGMA trusted_schema=OFF')
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or not db.execute('SELECT 1 FROM runs WHERE id=?', (owner['scope']['run_id'],)).fetchone(): reject('pilot_restore_database_invalid')
            finally: db.close()
            # Both independent deny and pending intent must complete their file
            # and parent-directory fsync before touching the original database.
            # A failure before that boundary aborts the restore; this is not a
            # claim of power-loss testing or cross-file atomic transactions.
            atomic(root/'revocation.json', {'revoked': True, 'reason': 'offline_restore_requires_owner_reconciliation', 'restore_request_id': request_id, 'at': time.time()})
            journal['requests'][request_id] = {'state': 'pending', 'archive_sha256': archive_sha}; atomic(journal_path, journal)
            workspace = directory(root/'workspace', create=True)
            for suffix in ('-wal', '-shm', '-journal'):
                old = workspace/('workflow.sqlite3'+suffix)
                if regular(old, missing=True): old.unlink()
            staged = workspace/('.restored-'+uuid.uuid4().hex+'.sqlite3'); shutil.copyfile(temp/'workflow.sqlite3', staged); os.chmod(staged, 0o600)
            fsync_file(staged); replace_durable(staged, workspace/'workflow.sqlite3')
            restored_config = read(temp/'service.json'); restored_config['workers'][WORKER]['revoked'] = True
            atomic(root/'service.json', restored_config); atomic(workspace/'initial-profile.json', read(temp/'workspace/initial-profile.json'))
            journal['requests'][request_id]['state'] = 'complete'; atomic(journal_path, journal)
    return {'restored': True, 'original_restore_reused': False, 'revoked': True, 'new_key_created': False}


def serve(env):
    root = root_path(env)
    with lock(root, '.runtime.lock', nonblocking=True):
        if env.get('ENGINE_RESTORE_ARCHIVE') or env.get('ENGINE_RESTORE_REQUEST_ID'): restore(root, env)
        prepared = prepare(env)
        origin = env.get('ENGINE_PUBLIC_ORIGIN') or env.get('RENDER_EXTERNAL_URL', '')
        u = public_url(origin)
        if u.path not in ('', '/') or u.query: reject('pilot_plain_https_origin_required')
        child_env = dict(env); child_env.update(ENGINE_WORKSPACE=str(root/'workspace'), ENGINE_CONFIG=str(root/'service.json'),
            ENGINE_ORIGIN=origin.rstrip('/'), ENGINE_BIND_HOST='0.0.0.0', ENGINE_PYTHON=sys.executable, NULUNO_METHOD_PACK_DIR=str(root/'method-packs'))
        interval = int(env.get('ENGINE_BACKUP_INTERVAL_SECONDS', '86400')); keep = int(env.get('ENGINE_BACKUP_KEEP', '3'))
        if not 3600 <= interval <= 604800 or not 1 <= keep <= 10: reject('pilot_backup_policy_invalid')
        stop = threading.Event(); child = subprocess.Popen(['node', '/opt/engine/deploy/mcp-gateway/server.mjs'], env=child_env)
        def scheduled():
            while not stop.wait(interval):
                try: backup(root, keep)
                except Exception: print(json.dumps({'event': 'pilot_backup_failed', 'action': 'operator_check_required'}), flush=True)
        thread = threading.Thread(target=scheduled, daemon=True); thread.start()
        def terminate(*_):
            stop.set(); child.terminate()
        signal.signal(signal.SIGTERM, terminate); signal.signal(signal.SIGINT, terminate)
        print(json.dumps({'event': 'pilot_started', **prepared, 'backup_interval_seconds': interval, 'runtime_source_commit': env.get('ENGINE_SOURCE_COMMIT'),
                          'multi_tenant': False, 'model_worker': False}), flush=True)
        try: return child.wait()
        finally: stop.set(); thread.join(timeout=2)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('command', choices=['check-mount', 'prepare', 'serve', 'backup', 'revoke']); args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.command != 'check-mount' and os.getuid() == 0:
            os.setgroups([]); os.setgid(10001); os.setuid(10001)
        root = root_path(os.environ)
        if args.command == 'check-mount': value = {'persistent_mount_checked': True}
        elif args.command == 'serve': return serve(os.environ)
        elif args.command == 'prepare': value = prepare(os.environ)
        elif args.command == 'revoke': value = revoke(root)
        else: value = backup(root)
        print(json.dumps(value)); return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch('pilot_[a-z_]+', str(exc)) else 'pilot_start_or_admin_operation_rejected'
        print(json.dumps({'error': code, 'state_preserved': True}), file=sys.stderr); return 1


if __name__ == '__main__': sys.exit(main())
