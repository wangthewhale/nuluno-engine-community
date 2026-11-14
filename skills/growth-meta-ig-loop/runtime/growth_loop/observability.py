"""Durable execution measurements; task success is never a sale or human claim."""
import json
import math
import re
import time
import uuid
from .files import canonical, digest
from .errors import WorkflowError


class ExecutionMetrics:
    def __init__(self, store, settings=None):
        self.store, self.settings = store, settings or {}
        store.db.execute('''CREATE TABLE IF NOT EXISTS engine_invocations(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL,worker TEXT NOT NULL,method TEXT NOT NULL,
            task_key TEXT,started REAL NOT NULL,finished REAL,elapsed_ms REAL,
            outcome TEXT NOT NULL,error_kind TEXT,resumed_original INTEGER NOT NULL DEFAULT 0)''')
        store.db.execute('''CREATE TABLE IF NOT EXISTS engine_human_receipts(
            id TEXT PRIMARY KEY,run_id TEXT NOT NULL,body TEXT NOT NULL,source_digest TEXT NOT NULL)''')

    def begin(self, run_id, worker, method, params):
        ident = uuid.uuid4().hex
        self.store.db.execute('INSERT INTO engine_invocations(id,run_id,worker,method,task_key,started,outcome) VALUES(?,?,?,?,?,?,?)',
            (ident, run_id, worker, method, params.get('task_key'), self.store.clock(), 'unknown_in_progress'))
        return ident, time.monotonic()

    def finish(self, mark, result=None, error=None):
        ident, monotonic = mark
        self.store.db.execute('UPDATE engine_invocations SET finished=?,elapsed_ms=?,outcome=?,error_kind=?,resumed_original=? WHERE id=?',
            (self.store.clock(), max(0, (time.monotonic()-monotonic)*1000), 'error' if error else 'returned',
             getattr(error, 'kind', type(error).__name__) if error else None,
             int(isinstance(result, dict) and result.get('resumed_original_lease') is True), ident))
        if isinstance(result,dict) and result.get('reused_original_receipt') is True:
            row=self.store.db.execute('SELECT run_id FROM engine_invocations WHERE id=?',(ident,)).fetchone()
            if row:self.store.event(row['run_id'],None,'public_source_receipt_reused',{'request_id':result.get('request_id'),'receipt_id':result.get('receipt_id')})

    def import_human_receipt(self, run_id, receipt_id):
        row = self.settings.get('interaction_receipts', {}).get(receipt_id)
        if not isinstance(row, dict) or row.get('run_id') != run_id or row.get('kind') not in ('human_takeover_started','human_takeover_completed'):
            raise WorkflowError('Original host interaction receipt required.', 'scope_mismatch')
        if not row.get('source_ref') or not re.fullmatch('[a-f0-9]{64}', str(row.get('raw_sha256',''))) or type(row.get('observed_at')) not in (int,float) or not math.isfinite(row['observed_at']) or not 0 <= row['observed_at'] <= self.store.clock():
            raise ValueError('Original interaction source binding required')
        source_digest = digest(row)
        old = self.store.db.execute('SELECT run_id,source_digest FROM engine_human_receipts WHERE id=?',(receipt_id,)).fetchone()
        if old and (old['run_id'] != run_id or old['source_digest'] != source_digest):
            raise ValueError('Original interaction receipt changed')
        self.store.db.execute('INSERT OR IGNORE INTO engine_human_receipts VALUES(?,?,?,?)',(receipt_id,run_id,canonical(row),source_digest))
        return {'receipt_id':receipt_id,'duplicate':bool(old),'observed_human_event':row['kind']}

    def summary(self, run_id):
        self.store.config(run_id)
        run = self.store.db.execute('SELECT created FROM runs WHERE id=?',(run_id,)).fetchone()
        calls = self.store.db.execute('SELECT * FROM engine_invocations WHERE run_id=? ORDER BY started,rowid',(run_id,)).fetchall()
        groups = {}
        for row in calls:
            item = groups.setdefault(row['method'], {'calls':0,'returned':0,'errors':0,'unknown_in_progress':0,'recorded_elapsed_ms':0})
            item['calls'] += 1; item[{'returned':'returned','error':'errors'}.get(row['outcome'],'unknown_in_progress')] += 1
            if row['elapsed_ms'] is not None: item['recorded_elapsed_ms'] += row['elapsed_ms']
        first = self.store.db.execute("SELECT MIN(created) FROM events WHERE run_id=? AND event='task_done'",(run_id,)).fetchone()[0]
        read = self.store.db.execute("SELECT MIN(created) FROM artifacts WHERE run_id=? AND kind LIKE 'public_source.%'",(run_id,)).fetchone()[0]
        gates = self.store.db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND event IN ('task_waiting_owner','task_waiting_approval')",(run_id,)).fetchone()[0]
        recovered = self.store.db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND event='expired_lease_recovered'",(run_id,)).fetchone()[0]
        public_reads={'attempts':0,'saved_completed_requests':0,'unknown_or_failed_requests':0}
        if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='public_read_requests'").fetchone():
            for r in self.store.db.execute('SELECT state,attempts FROM public_read_requests WHERE run_id=?',(run_id,)):
                public_reads['attempts']+=r['attempts'];public_reads['saved_completed_requests' if r['state']=='completed' else 'unknown_or_failed_requests']+=1
        public_reads['original_receipt_reuses']=self.store.db.execute("SELECT COUNT(*) FROM events WHERE run_id=? AND event='public_source_receipt_reused'",(run_id,)).fetchone()[0]
        confirmed = [];unbound=0
        for row in self.store.db.execute('SELECT id,body,source_digest FROM engine_human_receipts WHERE run_id=?',(run_id,)):
            original = self.settings.get('interaction_receipts',{}).get(row['id'])
            if isinstance(original,dict) and digest(original)==row['source_digest']: confirmed.append(json.loads(row['body']))
            else:unbound+=1
        coverage = self.settings.get('interaction_coverage',{}).get(run_id,{})
        covered = not unbound and coverage.get('run_id')==run_id and coverage.get('complete') is True and bool(coverage.get('source_ref')) and bool(re.fullmatch('[a-f0-9]{64}',str(coverage.get('raw_sha256','')))) and type(coverage.get('observed_at')) in (int,float) and math.isfinite(coverage['observed_at']) and 0<=coverage['observed_at']<=self.store.clock()
        return {'run_id':run_id,'measurement_scope':'saved Engine execution only; external host activity requires original host receipts',
            'first_completed_task_seconds':max(0,first-run['created']) if first is not None else None,
            'first_public_source_seconds':max(0,read-run['created']) if read is not None else None,
            'completed_tasks':self.store.db.execute("SELECT COUNT(*) FROM tasks WHERE run_id=? AND state='done'",(run_id,)).fetchone()[0],
            'owner_checkpoint_events':gates,'human_interventions':sum(r['kind']=='human_takeover_started' for r in confirmed) if covered else None,
            'observed_human_receipts':len(confirmed),'unbound_human_receipts':unbound,'human_coverage_complete':covered,
            'expired_lease_recoveries':recovered,'successful_original_lease_resumes':sum(r['resumed_original'] for r in calls),
            'public_reads':public_reads,
            'calls_by_method':groups,'commercial_success_inferred':False,'missing_history_backfilled':False}
