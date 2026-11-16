"""Real local SQLite lifecycle; no Render service, Docker claim or provider call."""
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pilot', ROOT/'pilot.py'); pilot = importlib.util.module_from_spec(spec); spec.loader.exec_module(pilot)


class PilotLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.env = {'ENGINE_STATE_DIR': str(self.root), 'ENGINE_PRODUCT_URL': 'https://product.example',
                    'ENGINE_PILOT_BEARER': secrets.token_urlsafe(32), 'ENGINE_PUBLIC_READ_LIMIT': '12'}
    def tearDown(self): self.tmp.cleanup()
    def prepare(self): return pilot.prepare(self.env, enforce_mount=False)
    def db(self): return sqlite3.connect(self.root/'workspace/workflow.sqlite3')
    def task_state(self):
        with self.db() as db: return db.execute('SELECT key,state,attempts FROM tasks ORDER BY key').fetchall()

    def test_first_boot_and_restart_preserve_original_run_tasks_and_quota(self):
        self.assertFalse(self.prepare()['already_created']); before = self.task_state()
        with self.db() as db:
            db.execute('CREATE TABLE public_read_requests(run_id,id,attempts)'); db.execute('INSERT INTO public_read_requests VALUES(?,?,?)', ('pilot-product', 'original', 7))
        second = self.prepare(); self.assertTrue(second['already_created']); self.assertFalse(second['state_reset'])
        self.assertEqual(self.task_state(), before)
        with self.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT attempts FROM public_read_requests').fetchone()[0], 7)
        config = pilot.read(self.root/'service.json')['workers'][pilot.WORKER]
        self.assertNotIn('run_create', config['methods']); self.assertNotIn('tool_execute', config['methods'])
        self.assertFalse(config['allow_evolution']); self.assertFalse(config['allow_goal_continuity'])
        self.assertEqual(config['tool_routes']['public-source']['allowed_urls'], ['https://product.example'])
        self.assertEqual((self.root/'service.json').stat().st_mode & 0o777, 0o600)

    def test_changed_secret_scope_or_unknown_state_never_reinitialize(self):
        self.prepare(); before = (self.root/'service.json').read_bytes()
        for changed in [{**self.env, 'ENGINE_PILOT_BEARER': secrets.token_urlsafe(32)}, {**self.env, 'ENGINE_PUBLIC_READ_LIMIT': '13'}, {**self.env, 'ENGINE_PRODUCT_URL': 'https://different.example'}]:
            with self.assertRaisesRegex(ValueError, 'original_owner_scope_or_secret_changed'): pilot.prepare(changed, False)
        self.assertEqual((self.root/'service.json').read_bytes(), before)

    def test_revocation_survives_boot_and_deny_tombstone_repairs_partial_write(self):
        self.prepare(); tasks = self.task_state(); pilot.revoke(self.root)
        config = pilot.read(self.root/'service.json'); config['workers'][pilot.WORKER].pop('revoked'); pilot.atomic(self.root/'service.json', config)
        self.assertTrue(self.prepare()['revoked']); self.assertTrue(pilot.read(self.root/'service.json')['workers'][pilot.WORKER]['revoked'])
        self.assertEqual(self.task_state(), tasks)

    def test_missing_original_database_is_not_replaced_by_empty_state(self):
        self.prepare(); (self.root/'workspace/workflow.sqlite3').unlink()
        with self.assertRaisesRegex(ValueError, 'original_database_missing'): self.prepare()
        self.assertFalse((self.root/'workspace/workflow.sqlite3').exists())

    def test_interrupted_first_boot_resumes_same_owner_intent(self):
        from nuluno_engine_community.growth_loop.service import Service
        with patch.object(Service, 'run', side_effect=RuntimeError('controlled first boot interruption')):
            with self.assertRaises(RuntimeError): self.prepare()
        self.assertEqual(pilot.read(self.root/'owner.json')['phase'], 'pending')
        self.prepare(); self.assertEqual(pilot.read(self.root/'owner.json')['phase'], 'complete')
        with self.db() as db: self.assertEqual(db.execute('SELECT count(*) FROM runs').fetchone()[0], 1)

    def test_unknown_directory_symlink_and_secret_url_are_rejected(self):
        (self.root/'workspace').mkdir()
        with self.assertRaisesRegex(ValueError, 'unknown_existing_state_preserved'): self.prepare()
        for value in ['https://product.example/?token=CONTROLLED_DO_NOT_ECHO', 'https://user:password@product.example']:
            with self.assertRaises(ValueError) as caught: pilot.scope({**self.env, 'ENGINE_PRODUCT_URL': value})
            self.assertNotIn('CONTROLLED_DO_NOT_ECHO', str(caught.exception))
        self.assertEqual(pilot.scope({**self.env, 'ENGINE_SOURCE_URLS_JSON': '["https://product.example/?lang=en"]'})['source_urls'], ['https://product.example/?lang=en'])

    def test_symlink_configuration_cannot_redirect_boot_or_revocation(self):
        self.prepare()
        with tempfile.TemporaryDirectory() as elsewhere:
            target = Path(elsewhere)/'outside.json'; target.write_text('{"outside":"unchanged"}')
            (self.root/'service.json').unlink(); (self.root/'service.json').symlink_to(target)
            with self.assertRaisesRegex(ValueError, 'symlink_rejected'): self.prepare()
            with self.assertRaisesRegex(ValueError, 'symlink_rejected'): pilot.revoke(self.root)
            self.assertEqual(target.read_text(), '{"outside":"unchanged"}')

    def test_backup_online_consistent_restore_revokes_and_does_not_repeat(self):
        self.prepare()
        with self.db() as db: db.execute('CREATE TABLE original_marker(id PRIMARY KEY,value)'); db.execute('INSERT INTO original_marker VALUES(1,"before")')
        saved = pilot.backup(self.root); archive = self.root/'backups'/saved['backup']
        with tarfile.open(archive) as t:
            for member in t:
                data = t.extractfile(member).read(); self.assertNotIn(self.env['ENGINE_PILOT_BEARER'].encode(), data)
            self.assertNotIn('workflow.sqlite3-wal', t.getnames())
        with self.db() as db: db.execute('UPDATE original_marker SET value="after"')
        env = {**self.env, 'ENGINE_RESTORE_REQUEST_ID': str(uuid.uuid4()), 'ENGINE_RESTORE_ARCHIVE': saved['backup']}
        with pilot.lock(self.root, '.runtime.lock', True): self.assertTrue(pilot.restore(self.root, env)['restored'])
        self.assertTrue(self.prepare()['revoked'])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT value FROM original_marker').fetchone()[0], 'before')
            db.execute('UPDATE original_marker SET value="post-restore"')
        with pilot.lock(self.root, '.runtime.lock', True): self.assertTrue(pilot.restore(self.root, env)['original_restore_reused'])
        with self.db() as db: self.assertEqual(db.execute('SELECT value FROM original_marker').fetchone()[0], 'post-restore')

    def test_one_runtime_lock_and_mount_enforcement_are_real(self):
        with self.assertRaisesRegex(ValueError, 'persistent_state_mount_required'): pilot.root_path(self.env)
        with pilot.lock(self.root, '.runtime.lock', True):
            with self.assertRaises(BlockingIOError):
                with pilot.lock(self.root, '.runtime.lock', True): pass

    def test_altered_backup_payload_or_link_is_rejected_before_restore(self):
        self.prepare(); saved = pilot.backup(self.root); archive = self.root/'backups'/saved['backup']
        with tarfile.open(archive) as source: contents = {m.name: source.extractfile(m).read() for m in source}
        before = (self.root/'workspace/workflow.sqlite3').read_bytes()
        for variant in ['hash', 'link']:
            bad = self.root/'backups'/('backup-bad-'+variant+'.tar.gz')
            with tarfile.open(bad, 'w:gz') as target:
                for name, raw in contents.items():
                    info = tarfile.TarInfo(name)
                    if variant == 'link' and name == 'owner.json': info.type = tarfile.SYMTYPE; info.linkname = '../../outside'; target.addfile(info); continue
                    if variant == 'hash' and name == 'workflow.sqlite3': raw += b'controlled tamper'
                    info.size = len(raw); target.addfile(info, io.BytesIO(raw))
            env = {**self.env, 'ENGINE_RESTORE_REQUEST_ID': str(uuid.uuid4()), 'ENGINE_RESTORE_ARCHIVE': bad.name}
            with self.assertRaisesRegex(ValueError, 'backup_hash_mismatch|backup_member_rejected'):
                with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
            self.assertEqual((self.root/'workspace/workflow.sqlite3').read_bytes(), before)
            self.assertFalse((self.root/'restore-journal.json').exists())

    def test_atomic_file_fsync_precedes_replace_and_parent_directory_fsync(self):
        events = []; fsync = os.fsync; replace = os.replace
        def sync(fd):
            kind = 'directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file'
            fsync(fd); events.append(('fsync', kind))
        def move(source, target):
            replace(source, target); events.append(('replace', Path(target).name))
        with patch.object(pilot.os, 'fsync', side_effect=sync), patch.object(pilot.os, 'replace', side_effect=move):
            pilot.atomic(self.root/'ordered.json', {'value': 1})
        self.assertEqual(events, [('fsync', 'file'), ('replace', 'ordered.json'), ('fsync', 'directory')])
        self.assertEqual((self.root/'ordered.json').stat().st_mode & 0o777, 0o600)

    def test_revocation_directory_sync_failure_aborts_before_configuration_change(self):
        self.prepare(); before = (self.root/'service.json').read_bytes(); tasks = self.task_state()
        with patch.object(pilot, 'fsync_directory', side_effect=OSError('controlled parent fsync failure')):
            with self.assertRaises(OSError): pilot.revoke(self.root)
        self.assertEqual((self.root/'service.json').read_bytes(), before)
        self.assertEqual(self.task_state(), tasks)
        # Visibility after a process-level fault is not proof of power-loss durability.
        self.assertTrue(pilot.read(self.root/'revocation.json')['revoked'])
        self.assertTrue(self.prepare()['revoked'])

    def restore_fixture(self):
        self.prepare()
        with self.db() as db:
            db.execute('CREATE TABLE durability_marker(value)'); db.execute('INSERT INTO durability_marker VALUES("before")')
        saved = pilot.backup(self.root)
        with self.db() as db: db.execute('UPDATE durability_marker SET value="after"')
        return {**self.env, 'ENGINE_RESTORE_REQUEST_ID': str(uuid.uuid4()), 'ENGINE_RESTORE_ARCHIVE': saved['backup']}

    def test_restore_durable_deny_and_pending_journal_precede_database_and_completed_journal(self):
        env = self.restore_fixture(); events = []
        atomic = pilot.atomic; file_sync = pilot.fsync_file; directory_sync = pilot.fsync_directory; replace = os.replace
        def write(path, value):
            atomic(path, value)
            if path == self.root/'revocation.json': events.append('deny_durable')
            elif path == self.root/'restore-journal.json': events.append('journal_'+value['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state']+'_durable')
            elif path == self.root/'service.json': events.append('config_durable')
        def sync_file(path):
            file_sync(path)
            if path.name.startswith('.restored-'): events.append('staged_database_fsynced')
        def move(source, target):
            replace(source, target)
            if Path(target) == self.root/'workspace/workflow.sqlite3': events.append('database_replaced')
        def sync_directory(path):
            directory_sync(path)
            if path == self.root/'workspace' and 'database_replaced' in events: events.append('workspace_directory_fsynced')
        with patch.object(pilot, 'atomic', side_effect=write), patch.object(pilot, 'fsync_file', side_effect=sync_file), patch.object(pilot, 'fsync_directory', side_effect=sync_directory), patch.object(pilot.os, 'replace', side_effect=move):
            with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
        required = ['deny_durable', 'journal_pending_durable', 'staged_database_fsynced', 'database_replaced', 'workspace_directory_fsynced', 'config_durable', 'journal_complete_durable']
        self.assertEqual([events.index(name) for name in required], sorted(events.index(name) for name in required))

    def test_restore_pending_journal_sync_failure_preserves_original_database_and_configuration(self):
        env = self.restore_fixture(); before_db = (self.root/'workspace/workflow.sqlite3').read_bytes(); before_config = (self.root/'service.json').read_bytes()
        sync = pilot.fsync_directory
        def fail_pending(path):
            if path == self.root and (self.root/'restore-journal.json').exists(): raise OSError('controlled pending journal directory fsync failure')
            sync(path)
        with patch.object(pilot, 'fsync_directory', side_effect=fail_pending):
            with self.assertRaises(OSError):
                with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
        self.assertEqual((self.root/'workspace/workflow.sqlite3').read_bytes(), before_db)
        self.assertEqual((self.root/'service.json').read_bytes(), before_config)
        self.assertTrue(pilot.read(self.root/'revocation.json')['revoked'])
        self.assertEqual(pilot.read(self.root/'restore-journal.json')['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state'], 'pending')
        self.assertTrue(self.prepare()['revoked'])

    def test_restore_staged_database_fsync_failure_does_not_replace_original_or_complete_request(self):
        env = self.restore_fixture(); before_db = (self.root/'workspace/workflow.sqlite3').read_bytes(); before_config = (self.root/'service.json').read_bytes()
        with patch.object(pilot, 'fsync_file', side_effect=OSError('controlled staged database fsync failure')):
            with self.assertRaises(OSError):
                with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
        self.assertEqual((self.root/'workspace/workflow.sqlite3').read_bytes(), before_db)
        self.assertEqual((self.root/'service.json').read_bytes(), before_config)
        self.assertEqual(pilot.read(self.root/'restore-journal.json')['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state'], 'pending')
        self.assertTrue(self.prepare()['revoked'])

    def test_restore_staging_failure_preserves_live_committed_wal_and_readable_rows(self):
        env = self.restore_fixture(); writer = self.db()
        try:
            writer.execute('PRAGMA journal_mode=WAL'); writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute('UPDATE durability_marker SET value="committed-in-live-wal"'); writer.commit()
            workspace = self.root/'workspace'
            before = {name: (workspace/name).read_bytes() for name in ('workflow.sqlite3', 'workflow.sqlite3-wal', 'workflow.sqlite3-shm')}
            self.assertGreater(len(before['workflow.sqlite3-wal']), 0)
            with patch.object(pilot, 'fsync_file', side_effect=OSError('controlled staged database fsync failure')):
                with self.assertRaises(OSError):
                    with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
            for name, raw in before.items(): self.assertEqual((workspace/name).read_bytes(), raw)
            self.assertEqual(list(workspace.glob('.restored-*')), [])
            with contextlib.closing(self.db()) as fresh:
                self.assertEqual(fresh.execute('SELECT value FROM durability_marker').fetchone()[0], 'committed-in-live-wal')
            self.assertTrue(self.prepare()['revoked'])
            self.assertEqual(writer.execute('SELECT value FROM durability_marker').fetchone()[0], 'committed-in-live-wal')
        finally: writer.close()

    def test_restore_busy_wal_aborts_before_replace_and_same_request_resumes_after_reader_closes(self):
        env = self.restore_fixture(); reader = self.db(); writer = self.db()
        try:
            reader.execute('BEGIN'); reader.execute('SELECT value FROM durability_marker').fetchone()
            writer.execute('UPDATE durability_marker SET value="after-reader-started"'); writer.commit()
            with self.assertRaisesRegex(ValueError, 'pilot_restore_original_database_busy'):
                with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
            self.assertEqual(writer.execute('SELECT value FROM durability_marker').fetchone()[0], 'after-reader-started')
            self.assertEqual(pilot.read(self.root/'restore-journal.json')['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state'], 'pending')
            self.assertTrue(pilot.read(self.root/'revocation.json')['revoked'])
            self.assertEqual(list((self.root/'workspace').glob('.restored-*')), [])
        finally: reader.close(); writer.close()
        with pilot.lock(self.root, '.runtime.lock', True): self.assertTrue(pilot.restore(self.root, env)['restored'])
        self.assertTrue(self.prepare()['revoked'])
        with contextlib.closing(self.db()) as fresh: self.assertEqual(fresh.execute('SELECT value FROM durability_marker').fetchone()[0], 'before')

    def test_restore_database_directory_sync_failure_keeps_pending_and_can_resume_same_request(self):
        env = self.restore_fixture(); before_config = (self.root/'service.json').read_bytes(); sync = pilot.fsync_directory
        def fail_workspace(path):
            if path == self.root/'workspace': raise OSError('controlled database directory fsync failure')
            sync(path)
        with patch.object(pilot, 'fsync_directory', side_effect=fail_workspace):
            with self.assertRaises(OSError):
                with pilot.lock(self.root, '.runtime.lock', True): pilot.restore(self.root, env)
        self.assertEqual((self.root/'service.json').read_bytes(), before_config)
        self.assertTrue(pilot.read(self.root/'revocation.json')['revoked'])
        self.assertEqual(pilot.read(self.root/'restore-journal.json')['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state'], 'pending')
        with pilot.lock(self.root, '.runtime.lock', True): self.assertTrue(pilot.restore(self.root, env)['restored'])
        self.assertEqual(pilot.read(self.root/'restore-journal.json')['requests'][env['ENGINE_RESTORE_REQUEST_ID']]['state'], 'complete')
        self.assertTrue(self.prepare()['revoked'])


if __name__ == '__main__': unittest.main()
