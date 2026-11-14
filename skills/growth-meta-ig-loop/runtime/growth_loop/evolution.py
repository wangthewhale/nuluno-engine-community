"""Autonomous evidence -> proposal -> trial -> promotion -> monitoring loop.

This changes procedures for future, already-authorized work in the existing
queue. It creates no marketing action, connector, account or extra scheduler.
"""
import json
import uuid
from collections import defaultdict
from .files import canonical, digest
from .errors import Waiting, WorkflowError
from .experience import Experience
from .model_adapter import ProposalModel
from .procedure_program import validate_program, OPERATIONS
from .adaptation import planning_scope


class EvolutionController:
    def __init__(self, store, config, model=None):
        self.store, self.config = store, config
        self.policy = config.get('evolution', {})
        self.experience = Experience(store, config)
        self.model = model or ProposalModel(store, self.policy.get('model', {}))
        store.db.execute('CREATE TABLE IF NOT EXISTS evolution_locks(brand TEXT PRIMARY KEY, token TEXT NOT NULL, expires REAL NOT NULL)')

    def enabled(self, run_id):
        if run_id not in self.config.get('runs', []):
            raise WorkflowError('演化控制器不屬於本工作。', 'scope_mismatch')
        return self.config.get('allow_evolution') is True and self.policy.get('enabled') is True

    def status(self, run_id):
        enabled = self.enabled(run_id)
        saved = self.store.artifact(run_id, 0, 'evolution.state') or {}
        brand = self.experience.brand(run_id)
        heads = [dict(r) for r in self.store.db.execute('SELECT context_hash,version,previous FROM procedure_heads WHERE brand=?', (brand,))]
        trials = [{'experiment_id': r['id'], 'result': json.loads(r['result']) if r['result'] else None} for r in self.store.db.execute('SELECT id,result FROM procedure_experiments WHERE brand=?', (brand,))]
        return {'version': 'nuluno-evolution-1', 'enabled': enabled, 'state': saved.get('state', 'idle' if enabled else 'disabled'),
                'last_transition': saved, 'heads': heads, 'trials': trials,
                'weights_updated': False, 'goal_achievement_inferred': False, 'extra_external_actions_created': False}

    def transition(self, run_id, state, **values):
        body = {'state': state, 'at': self.store.clock(), **values}
        self.store.artifact(run_id, 0, 'evolution.state', body)
        self.store.event(run_id, None, 'evolution.' + state, body)
        return body

    def tick(self, run_id):
        if not self.enabled(run_id):
            return self.status(run_id)
        if (self.store.root / '.transfer.lock').exists():
            return {**self.status(run_id), 'workspace_transfer_in_progress': True}
        if self.store.artifact(run_id, 0, 'graph.stopped'):
            self.transition(run_id, 'owner_stopped')
            return self.status(run_id)
        brand, now, token = self.experience.brand(run_id), self.store.clock(), uuid.uuid4().hex
        scope = planning_scope(self.store.config(run_id))
        original_policy = self.store.artifact(run_id, 0, 'evolution.policy')
        policy_hash = digest(self.policy)
        if original_policy and original_policy != {'scope': scope, 'policy_sha256': policy_hash}:
            self.transition(run_id, 'scope_changed', original_policy_preserved=True)
            return self.status(run_id)
        self.store.artifact(run_id, 0, 'evolution.policy', {'scope': scope, 'policy_sha256': policy_hash})
        with self.store.transaction():
            if (self.store.root / '.transfer.lock').exists():
                return {**self.status(run_id), 'workspace_transfer_in_progress': True}
            changed = self.store.db.execute('''INSERT INTO evolution_locks VALUES(?,?,?)
              ON CONFLICT(brand) DO UPDATE SET token=excluded.token,expires=excluded.expires
              WHERE evolution_locks.expires<=?''', (brand, token, now + 180, now)).rowcount
        if not changed:
            return {**self.status(run_id), 'controller_busy': True}
        try:
            self.experience.sync(run_id)
            self._compare_and_monitor(run_id)
            self._propose(run_id, scope, token)
        except Waiting as exc:
            self.transition(run_id, exc.kind, detail=str(exc))
        except Exception as exc:
            # Learning failure cannot erase or stop ordinary authorized work.
            self.transition(run_id, 'diagnostic_required', error_type=type(exc).__name__, detail=str(exc)[:600])
        finally:
            self.store.db.execute('DELETE FROM evolution_locks WHERE brand=? AND token=?', (brand, token))
        return self.status(run_id)

    def _compare_and_monitor(self, run_id):
        brand = self.experience.brand(run_id)
        for row in self.store.db.execute('SELECT id,body,result FROM procedure_experiments WHERE brand=?', (brand,)).fetchall():
            plan = json.loads(row['body'])
            if not plan.get('autonomous_controller'):
                continue
            result = self.experience.compare(run_id, row['id'])
            if result['decision'] == 'adopt':
                head = self.store.db.execute('SELECT version FROM procedure_heads WHERE brand=? AND context_hash=?', (brand, plan['context_hash'])).fetchone()
                if not head or head['version'] == plan['baseline']:
                    self.experience.adopt(run_id, row['id'])
                    self.store.artifact(run_id, 0, 'evolution.promoted.' + plan['candidate'], {'at': self.store.clock(), 'experiment_id': row['id'], 'monitor': plan['monitor']})
                    self.transition(run_id, 'adopted', procedure_id=plan['candidate'], experiment_id=row['id'], evidence_digest=result.get('evidence_digest'))
            elif result['decision'] in ('retain', 'inconclusive'):
                key = 'evolution.closed.' + row['id']
                if not self.store.artifact(run_id, 0, key):
                    self.store.artifact(run_id, 0, key, result)
                    self.transition(run_id, result['decision'], experiment_id=row['id'], reasons=result['reasons'])
        for head in self.store.db.execute('SELECT context_hash,version FROM procedure_heads WHERE brand=?', (brand,)).fetchall():
            marker = self.store.artifact(run_id, 0, 'evolution.promoted.' + head['version'])
            if not marker or head['version'] == 'original':
                continue
            groups = defaultdict(list)
            for row in self.store.db.execute('SELECT id,body FROM execution_episodes WHERE brand=? AND context_hash=? ORDER BY created,id', (brand, head['context_hash'])):
                episode = json.loads(row['body']); assignment = episode.get('assignment') or {}
                if assignment.get('arm') == 'adopted' and assignment.get('version') == head['version'] and episode.get('started_at', 0) >= marker['at']:
                    groups[episode['task_key']].append((row['id'], episode))
            cfg = marker['monitor']; minimum = cfg['minimum_tasks']
            if len(groups) < minimum:
                continue
            # Fixed non-overlapping windows, never cherry-picked successes.
            entries = list(groups.items())
            checked = int((self.store.artifact(run_id, 0, 'evolution.monitored.' + head['version']) or {}).get('tasks', 0))
            while len(entries) - checked >= minimum:
                window = entries[checked:checked + minimum]
                success = sum(any(e['outcome'] == 'success' for _, e in episodes) for _, episodes in window)
                if success / minimum < cfg['minimum_success_rate']:
                    evidence = [ident for _, episodes in window for ident, _ in episodes]
                    self.experience.rollback(run_id, head['version'], 'new_independent_execution_regression:' + digest(evidence))
                    self.transition(run_id, 'rolled_back', procedure_id=head['version'], episode_ids=evidence, successes=success, tasks=minimum)
                    break
                checked += minimum
                self.store.artifact(run_id, 0, 'evolution.monitored.' + head['version'], {'tasks': checked, 'at': self.store.clock()})

    def _propose(self, run_id, scope, token):
        previous = self.store.artifact(run_id, 0, 'evolution.state') or {}
        if previous.get('state') == 'rolled_back' and self.store.clock() - previous['at'] < self.policy.get('cooldown_seconds', 3600):
            return
        if self.store.db.execute('SELECT 1 FROM procedure_experiments WHERE brand=? AND result IS NULL', (self.experience.brand(run_id),)).fetchone():
            current = self.store.artifact(run_id, 0, 'evolution.state') or {}
            if current.get('state') not in ('adopted', 'rolled_back'):
                self.transition(run_id, 'waiting_fresh_samples')
            return
        grouped = defaultdict(list)
        for row in self.store.db.execute('SELECT id,body FROM execution_episodes WHERE brand=? ORDER BY created,id', (self.experience.brand(run_id),)):
            episode = json.loads(row['body'])
            if episode['run_id'] in self.config.get('runs', []) and episode['reusable']:
                grouped[digest(episode['context'])].append((row['id'], episode))
        maximum = self.policy.get('maximum_proposals', 3)
        if type(maximum) is not int or not 1 <= maximum <= 100:
            raise ValueError('演化提案數需事前固定在1至100之內')
        used = self.store.db.execute("SELECT count(*) FROM artifacts WHERE run_id=? AND kind LIKE 'evolution.proposal.%'", (run_id,)).fetchone()[0]
        for context_hash, episodes in grouped.items():
            episodes = episodes[-20:]
            if not any(e['outcome'] == 'success' for _, e in episodes) or not any(e['outcome'] == 'failure' for _, e in episodes):
                continue
            context = episodes[0][1]['context']; route = self.config.get('tool_routes', {}).get(context['route_id'], {})
            if route.get('mode') != 'read' or not route.get('learning_fields') or digest(route) != context['route_sha256']:
                continue
            head = self.store.db.execute('SELECT version FROM procedure_heads WHERE brand=? AND context_hash=?', (self.experience.brand(run_id), context_hash)).fetchone()
            baseline = head['version'] if head else 'original'
            failures = [ident for ident, e in episodes if e['outcome'] == 'failure']
            fingerprint = digest({'context': context_hash, 'baseline': baseline, 'failures': failures})
            key = 'evolution.proposal.' + fingerprint
            saved_proposal = self.store.artifact(run_id, 0, key)
            if saved_proposal and saved_proposal['state'] != 'proposing':
                continue
            if not saved_proposal and used >= maximum:
                return
            training = []
            for ident, episode in episodes:
                trace = self.store.artifact(episode['run_id'], 0, episode['trace_id'])
                if not trace:
                    row = self.store.db.execute('SELECT data FROM artifacts WHERE run_id=? AND kind=?', (episode['run_id'], episode['trace_id'])).fetchone()
                    trace = json.loads(row[0]) if row else {}
                training.append({'episode_id': ident, 'outcome': episode['outcome'], 'error_type': trace.get('error_type'), 'failure': trace.get('message'), 'input_examples': trace.get('learning_inputs', {}), 'source_class': episode['source']})
            baseline_program = self.experience.version(run_id, baseline).get('program', []) if baseline != 'original' else []
            prompt = {'context': context, 'allowed_operations': list(OPERATIONS), 'allowed_fields': route['learning_fields'], 'baseline_program': baseline_program, 'episodes': training}
            if not saved_proposal:
                self.store.artifact(run_id, 0, key, {'state': 'proposing', 'at': self.store.clock(), 'fingerprint': fingerprint})
            proposal = self.model.propose(run_id, 'evolution.model.' + fingerprint, prompt)
            lock = self.store.db.execute('SELECT token,expires FROM evolution_locks WHERE brand=?', (self.experience.brand(run_id),)).fetchone()
            if not lock or lock['token'] != token or lock['expires'] <= self.store.clock() or planning_scope(self.store.config(run_id)) != scope or self.store.artifact(run_id, 0, 'graph.stopped'):
                raise WorkflowError('提案期間工作範圍或租約已改變，保留提案但不採用。', 'scope_mismatch')
            known = {ident for ident, _ in episodes}
            if not isinstance(proposal.get('sources'), list) or not proposal['sources'] or set(proposal['sources']) - known:
                raise ValueError('候選方法需引用原始經驗ID')
            validate_program(proposal.get('program'), route)
            if not proposal['program'] or proposal['program'] == baseline_program:
                self.store.artifact(run_id, 0, key, {'state': 'no_behavioral_change', 'proposal': proposal})
                self.transition(run_id, 'no_justified_change', fingerprint=fingerprint)
                return
            if not isinstance(proposal.get('hypothesis'), str) or len(proposal['hypothesis']) < 12 or not isinstance(proposal.get('instruction'), str) or len(proposal['instruction']) < 20:
                raise ValueError('候選方法需具體指令與可被推翻的假說')
            now = self.store.clock()
            horizon = self.policy.get('trial_seconds', 86400)
            minimum = self.policy.get('minimum_sample', 8)
            monitor = self.policy.get('monitor', {'minimum_tasks': 8, 'minimum_success_rate': 0.75})
            if type(horizon) not in (int, float) or not 1 <= horizon <= 30 * 86400 or type(minimum) is not int or minimum < 4 or type(monitor.get('minimum_tasks')) is not int or monitor['minimum_tasks'] < 4 or not 0 <= monitor.get('minimum_success_rate', -1) <= 1:
                raise ValueError('實驗窗口、獨立樣本與回退門檻需事前固定')
            with self.store.transaction():
                candidate = self.experience.derive(run_id, [ident for ident, _ in episodes], proposal['instruction'], proposal['program'])
                body = {'candidate': candidate['procedure_id'], 'baseline': baseline, 'start_at': now, 'end_at': now + horizon,
                        'minimum_sample': minimum, 'minimum_improvement': self.policy.get('minimum_improvement', 0),
                        'maximum_latency_ratio': self.policy.get('maximum_latency_ratio', 5), 'hypothesis': proposal['hypothesis']}
                experiment = self.experience.register(run_id, body, controller={'autonomous_controller': 'nuluno-evolution-1', 'monitor': monitor},window_seconds=horizon)
                self.store.artifact(run_id, 0, key, {'state': 'trial_registered', 'proposal': proposal, 'procedure_id': candidate['procedure_id'], 'experiment_id': experiment['experiment_id'], 'at': now})
                self.transition(run_id, 'trial_registered', procedure_id=candidate['procedure_id'], experiment_id=experiment['experiment_id'])
            return
