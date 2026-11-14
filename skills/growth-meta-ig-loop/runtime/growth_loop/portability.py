"""Private, quiescent execution/experience snapshots; credentials stay external."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import zipfile
from .store import Store

GENERATED = {'workflow-inputs', 'workflow-requests', 'workflow-results', 'creative-requests', 'action-permits', 'media', 'outputs', 'assets'}
FORBIDDEN = {'credentials.json', 'authorization.json', '.env', '.venv', '.state', '.git', '__pycache__', '.transfer.lock'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_snapshot(workspace, destination):
    root, destination = Path(workspace).resolve(), Path(destination).resolve()
    if destination.exists() or root in destination.parents:
        raise ValueError('快照需新的私密輸出路徑，不能寫在原工作區內')
    lock = root / '.transfer.lock'
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
    store = None
    completed = False
    try:
        store = Store(root)
        now = store.clock()
        if store.db.execute("SELECT 1 FROM tasks WHERE state='running' AND lease_until>?", (now,)).fetchone():
            raise ValueError('工作者仍持有租約；先完成當前步驟，再搬移')
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_read_requests'").fetchone() and store.db.execute("SELECT 1 FROM public_read_requests WHERE state='dispatching' AND updated>?",(now-130,)).fetchone():
            raise ValueError('公開來源讀取仍在執行，先保留原讀取結果再搬移')
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='evolution_locks'").fetchone() and store.db.execute('SELECT 1 FROM evolution_locks WHERE expires>?', (now,)).fetchone():
            raise ValueError('演化控制器仍執行中，先保留原工作者')
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='engine-snapshot-', dir=str(destination.parent)) as temporary:
            staging = Path(temporary)
            copied = sqlite3.connect(str(staging / 'workflow.sqlite3'))
            try:
                store.db.backup(copied)
            finally:
                copied.close()
            for folder in GENERATED:
                source = root / folder
                if not source.exists():
                    continue
                for path in source.rglob('*'):
                    relative = path.relative_to(root)
                    if path.is_symlink():
                        raise ValueError('資料快照不能包含符號連結')
                    if path.is_file() and not any(part in FORBIDDEN or part.startswith('.env') for part in relative.parts):
                        target = staging / relative; target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, target)
            previous = root / 'workspace-origin.json'
            origins = json.loads(previous.read_text()).get('origins', []) if previous.exists() else []
            manifest = {'schema': 'nuluno-engine-snapshot-1', 'created_at': now, 'origins': list(dict.fromkeys(origins + [str(root)])),
                        'files': {str(p.relative_to(staging)): sha(p) for p in staging.rglob('*') if p.is_file()},
                        'credentials_included': False, 'authorization_grants_included': False}
            with zipfile.ZipFile(destination, 'x', zipfile.ZIP_DEFLATED) as archive:
                for path in staging.rglob('*'):
                    if path.is_file():
                        archive.write(path, str(path.relative_to(staging)))
                archive.writestr('snapshot.json', json.dumps(manifest, ensure_ascii=False))
            os.chmod(destination, 0o600)
        checksum=sha(destination)
        lock.write_text(json.dumps({'state':'frozen_for_transfer','snapshot_sha256':checksum,'created_at':now}))
        completed=True
        return {'path': str(destination), 'sha256': checksum, 'files': len(manifest['files']), 'credential_copy': False,
                'original_workspace_modified': True, 'business_records_modified': False, 'source_frozen_for_transfer': True}
    finally:
        if store is not None:
            store.close()
        if not completed:
            lock.unlink()


def restore_snapshot(source, workspace):
    source, root = Path(source).resolve(), Path(workspace).resolve()
    if root.exists():
        raise ValueError('只能還原至新的工作區，不能覆寫既有任務')
    root.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='engine-restore-', dir=str(root.parent)) as temporary:
        staging = Path(temporary) / 'state'; staging.mkdir(mode=0o700)
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 200 * 1024 * 1024:
                raise ValueError('快照含重複項目或超過容量限制')
            manifest = json.loads(archive.read('snapshot.json'))
            if manifest.get('schema') != 'nuluno-engine-snapshot-1' or set(names) != set(manifest['files']) | {'snapshot.json'}:
                raise ValueError('不是完整的引擎快照')
            for name, checksum in manifest['files'].items():
                path = Path(name)
                if path.is_absolute() or '..' in path.parts or any(part in FORBIDDEN or part.startswith('.env') for part in path.parts):
                    raise ValueError('快照路徑或私密內容不適用')
                raw = archive.read(name)
                if hashlib.sha256(raw).hexdigest() != checksum:
                    raise ValueError('快照資料校驗失敗')
                target = staging / path; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw); os.chmod(target, 0o600)
        if not (staging / 'workflow.sqlite3').is_file():
            raise ValueError('快照沒有原工作狀態')
        db = sqlite3.connect(str(staging / 'workflow.sqlite3'))
        try:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('原工作資料庫完整性檢查失敗')
        finally:
            db.close()
        (staging / 'workspace-origin.json').write_text(json.dumps({'origins': manifest['origins']}))
        staging.rename(root)
    return {'workspace': str(root), 'credentials_required_separately': True, 'original_operation_keys_preserved': True, 'source_sha256': sha(source)}


def local_path(store, value):
    path = Path(value).resolve()
    if store.root in path.parents:
        return path
    marker = store.root / 'workspace-origin.json'
    origins = json.loads(marker.read_text()).get('origins', []) if marker.exists() else []
    for origin in origins:
        try:
            relative = path.relative_to(Path(origin).resolve())
        except ValueError:
            continue
        candidate = (store.root / relative).resolve()
        if store.root in candidate.parents:
            return candidate
    raise ValueError('輸入檔案不屬於本工作區或已核對的原位置')
