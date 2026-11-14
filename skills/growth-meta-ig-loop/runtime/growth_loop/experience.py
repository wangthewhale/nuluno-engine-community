"""Trace-derived procedural skills with prospective, independently read evaluation.

Only operator-bound dispatch recipes can become active. Neither a model's
self-rating nor a successful tool call is evidence of customer demand.
"""
import json
import math
from .files import canonical, digest, redact
from .errors import WorkflowError


def _wilson(success, count):
    z = 1.959963984540054
    rate = success / count; denominator = 1 + z*z/count
    centre = (rate + z*z/(2*count)) / denominator
    half = z * math.sqrt(rate*(1-rate)/count + z*z/(4*count*count)) / denominator
    return [max(0, centre-half), min(1, centre+half)]


class Experience:
    def __init__(self, store, config=None):
        self.store, self.db, self.config = store, store.db, config or {}
        schema = '''
        CREATE TABLE IF NOT EXISTS execution_episodes(
          brand TEXT NOT NULL,id TEXT NOT NULL,run_id TEXT NOT NULL,task_key TEXT NOT NULL,
          skill_id TEXT NOT NULL,context_hash TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL,
          PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS procedure_versions(
          brand TEXT NOT NULL,id TEXT NOT NULL,skill_id TEXT NOT NULL,context_hash TEXT NOT NULL,
          body TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS procedure_experiments(
          brand TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,result TEXT,created REAL NOT NULL,
          PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS procedure_heads(
          brand TEXT NOT NULL,context_hash TEXT NOT NULL,version TEXT NOT NULL,previous TEXT,
          PRIMARY KEY(brand,context_hash));
        '''
        for statement in schema.split(';'):
            if statement.strip(): self.db.execute(statement)

    def brand(self, run_id):
        return self.store.config(run_id)['brand']['url']

    def capture(self, run_id, trace_id):
        if not isinstance(trace_id, str) or not trace_id.startswith(('tool.execution.', 'tool.failure.')):
            raise ValueError('只能讀取既有實際工具紀錄')
        row = self.db.execute('SELECT data,cycle FROM artifacts WHERE run_id=? AND kind=?', (run_id, trace_id)).fetchone()
        if not row: raise ValueError('本工作沒有此原始工具紀錄')
        trace = json.loads(row['data']); task = self.store.task(trace['task_key'])
        if not task or task['run_id'] != run_id: raise ValueError('工具紀錄與任務不符')
        skill = trace['skill_id']; profile = self.store.config(run_id)
        route = self.config.get('tool_routes', {}).get(trace['route_id'], {})
        if trace.get('route_sha256') != digest(route):
            # Legacy evidence remains inspectable, but cannot silently acquire
            # the semantics of a newer adapter.
            route = {}
        context = {'skill_id': skill, 'channel': route.get('channel'),
            'account_ref': profile.get('bindings', {}).get(route.get('channel'), {}).get('account_ref'),
            'route_id': trace['route_id'], 'route_sha256': trace.get('route_sha256'),
            'schema_sha256': trace.get('schema_sha256'), 'transport': trace.get('transport') or route.get('transport')}
        success = trace_id.startswith('tool.execution.')
        operation = self.store.operation(trace['operation_key']) if trace.get('operation_key') else None
        if operation and operation['state'] != 'succeeded': success = False
        assignment = self.store.artifact(run_id, 0, 'procedure.assignment.' + digest(task['key']))
        body = {'trace_id': trace_id, 'trace_sha256': digest(trace), 'run_id': run_id,
            'task_key': task['key'], 'skill_id': skill, 'context': context,
            'source': trace.get('source'), 'outcome': 'success' if success else 'failure',
            'failure_kind': trace.get('kind'), 'operation_key': trace.get('operation_key'),
            'operation_state': operation['state'] if operation else None,
            'started_at': trace.get('started_at'), 'finished_at': trace['observed_at'],
            'duration_ms': trace.get('duration_ms'), 'arguments_sha256': trace.get('arguments_sha256'),
            'attempt_ref':trace.get('attempt_ref'),
            'assignment': assignment, 'business_outcome_verified': False,
            'reusable': bool(route and context['schema_sha256'] and trace.get('source') in ('live_tool', 'controlled_fixture'))}
        ident = digest({'run': run_id, 'trace': trace_id})
        self.db.execute('INSERT OR IGNORE INTO execution_episodes VALUES(?,?,?,?,?,?,?,?)',
            (self.brand(run_id), ident, run_id, task['key'], skill, digest(context), canonical(redact(body)), self.store.clock()))
        return {'episode_id': ident, **body}

    def sync(self, run_id):
        ids = [r[0] for r in self.db.execute("SELECT kind FROM artifacts WHERE run_id=? AND (kind LIKE 'tool.execution.%' OR kind LIKE 'tool.failure.%') ORDER BY created,rowid", (run_id,))]
        return {'episodes': [self.capture(run_id, ident)['episode_id'] for ident in ids], 'business_outcomes_inferred': False}

    def episodes(self, run_id, skill_id=None, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('無效分頁')
        rows = self.db.execute('SELECT rowid,id,body FROM execution_episodes WHERE brand=? AND rowid>? AND (? IS NULL OR skill_id=?) ORDER BY rowid LIMIT ?',
            (self.brand(run_id), after, skill_id, skill_id, limit+1)).fetchall()
        return {'items': [{'episode_id': r['id'], **json.loads(r['body'])} for r in rows[:limit]],
                'next_cursor': rows[limit-1]['rowid'] if len(rows) > limit else None}

    def derive(self, run_id, episode_ids, instruction=None, program=None):
        if not isinstance(episode_ids, list) or not 1 <= len(episode_ids) <= 100 or len(set(episode_ids)) != len(episode_ids):
            raise ValueError('需1至100個不重複的原始經驗ID')
        bodies = []
        for ident in episode_ids:
            row = self.db.execute('SELECT body FROM execution_episodes WHERE brand=? AND id=?', (self.brand(run_id), ident)).fetchone()
            if not row: raise ValueError('經驗不屬於本品牌')
            bodies.append(json.loads(row[0]))
        contexts = {digest(e['context']) for e in bodies}
        if len(contexts) != 1 or any(not e['reusable'] for e in bodies) or not any(e['outcome'] == 'success' for e in bodies):
            raise ValueError('需同工具／schema／帳號的已驗證成功經驗；舊版或僅失敗紀錄不足')
        sources = {e['source'] for e in bodies}
        if len(sources) != 1: raise ValueError('受控與真實經驗不能混用')
        context = bodies[0]['context']; route = self.config.get('tool_routes', {}).get(context['route_id'])
        if not route or digest(route) != context['route_sha256']:
            raise ValueError('目前工具已變更，需重新取得實際經驗')
        if instruction is not None and (not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 4000):
            raise ValueError('模型補充方法需1至4000字；不能提供可執行程式')
        errors = sorted({e['failure_kind'] for e in bodies if e.get('failure_kind')})
        steps = [
            {'step': 1, 'action': '核對同品牌、同帳號及此版本的工具schema；不新增權限', 'route_id': context['route_id'], 'schema_sha256': context['schema_sha256']},
            {'step': 2, 'action': '依宿主實際schema組合本次參數；沿用原技能輸入／輸出契約', 'tool_name': route['tool_name']},
            {'step': 3, 'action': '對外寫入先保留原operation_key；不明結果只對帳，不重送', 'write_readback': bool(route.get('readback'))},
            {'step': 4, 'action': '核對原生輸出或本機檔案；與本次任務、帳號及參數摘要匹配', 'proof': 'new tool.execution trace; original write readback where applicable'},
            {'step': 5, 'action': '回填具體技能成果與原始證據；操作成功不得代替買方或收入結果', 'known_failure_kinds': errors}]
        body = {'schema': 'growth-derived-procedure-v1', 'skill_id': bodies[0]['skill_id'], 'context': context,
                'training_episodes': episode_ids, 'source_class': bodies[0]['source'], 'steps': steps,
                'model_instruction': instruction, 'core_contract_mutable': False,
                'created_at': self.store.clock(), 'business_success_claim': False}
        if program is not None:
            from .procedure_program import validate_program
            body['program'] = validate_program(program, route)
        ident = digest(body); context_hash = digest(context)
        self.db.execute('INSERT OR IGNORE INTO procedure_versions VALUES(?,?,?,?,?,?,?)',
            (self.brand(run_id), ident, body['skill_id'], context_hash, canonical(redact(body)), 'candidate', self.store.clock()))
        number=self.db.execute('SELECT rowid FROM procedure_versions WHERE brand=? AND id=?',(self.brand(run_id),ident)).fetchone()[0]
        return {'procedure_id': ident, 'label': 'M-' + str(number).zfill(6), 'state': 'candidate', **body}

    def version(self, run_id, ident):
        row = self.db.execute('SELECT body,state,context_hash FROM procedure_versions WHERE brand=? AND id=?', (self.brand(run_id), ident)).fetchone()
        if not row: raise ValueError('沒有本品牌的方法版本')
        return {'procedure_id': ident, 'state': row['state'], 'context_hash': row['context_hash'], **json.loads(row['body'])}

    def register(self, run_id, body, controller=None, window_seconds=None):
        required = {'candidate', 'baseline', 'start_at', 'end_at', 'minimum_sample', 'minimum_improvement', 'maximum_latency_ratio', 'hypothesis'}
        if not isinstance(body, dict) or set(body) != required or not body['hypothesis']:
            raise ValueError('需完整事前執行實驗、樣本、可靠度與延遲限制')
        body = json.loads(canonical(body)); candidate = self.version(run_id, body['candidate'])
        registered_at=self.store.clock()
        if window_seconds is not None:
            if controller is None or type(window_seconds) not in (int,float) or not math.isfinite(window_seconds) or not 1<=window_seconds<=30*86400:
                raise ValueError('只有內部控制器可在登記當下開始事前固定的窗口')
            # The controller holds BEGIN IMMEDIATE: no new assignment can be
            # enrolled before this transaction commits. Pin time here rather
            # than comparing a pre-derive timestamp with a later wall clock.
            body.update(start_at=registered_at,end_at=registered_at+window_seconds)
        for key in ('start_at', 'end_at', 'minimum_improvement', 'maximum_latency_ratio'):
            if type(body[key]) not in (int, float) or not math.isfinite(body[key]): raise ValueError('窗口／門檻需有限數值')
        if body['start_at'] < registered_at or body['end_at'] <= body['start_at'] or not 0 <= body['minimum_improvement'] <= 1 or body['maximum_latency_ratio'] < 1:
            raise ValueError('需事前登記有效窗口、0至1可靠度改善與至少1的延遲倍率')
        if type(body['minimum_sample']) is not int or body['minimum_sample'] < 2: raise ValueError('每組至少兩個獨立任務；不可把同任務重試算新樣本')
        if body['baseline'] != 'original':
            baseline = self.version(run_id, body['baseline'])
            if baseline['context_hash'] != candidate['context_hash']: raise ValueError('基準與候選的工具／帳號不同')
        head = self.db.execute('SELECT version FROM procedure_heads WHERE brand=? AND context_hash=?', (self.brand(run_id), candidate['context_hash'])).fetchone()
        if body['baseline'] != (head[0] if head else 'original'): raise ValueError('比較需從目前採用版本出發')
        for row in self.db.execute('SELECT body,result FROM procedure_experiments WHERE brand=?', (self.brand(run_id),)):
            old = json.loads(row[0])
            if old['context_hash'] == candidate['context_hash'] and not row[1] and max(old['start_at'], body['start_at']) < min(old['end_at'], body['end_at']):
                raise ValueError('同一方法範圍不可有重疊的未結案實驗')
        body.update(context=candidate['context'], context_hash=candidate['context_hash'],
                    family='execution', metric='tool_task_success_rate', source_class=candidate['source_class'],
                    assignment='preregistered_task_hash', causality_claim=False)
        if controller is not None:
            if set(controller) != {'autonomous_controller', 'monitor'} or controller['autonomous_controller'] != 'nuluno-evolution-1':
                raise ValueError('未知的演化控制器登記')
            body.update(controller)
        ident = digest(body)
        self.db.execute('INSERT OR IGNORE INTO procedure_experiments VALUES(?,?,?,?,?)', (self.brand(run_id), ident, canonical(body), None, self.store.clock()))
        return {'experiment_id': ident, **body}

    def assignment(self, task):
        """Pin instructions into a request before it is leased; never edit one."""
        run_id = task['run_id']; key = 'procedure.assignment.' + digest(task['key'])
        old = self.store.artifact(run_id, 0, key)
        if old: return old
        inventory = self.store.artifact(run_id, 0, 'capability.current') or {}
        skill = task['data'].get('channel_skill_id') or task['data'].get('skill_id')
        matches = [r for r in inventory.get('routes', []) if r['state'] == 'verified' and skill in r['skill_ids']]
        now = self.store.clock()
        for row in self.db.execute('SELECT id,body FROM procedure_experiments WHERE brand=? AND result IS NULL ORDER BY created,id', (self.brand(run_id),)):
            plan = json.loads(row['body']); ctx = plan['context']
            if skill != ctx['skill_id'] or not plan['start_at'] <= now < plan['end_at']: continue
            if not any(r['route_id'] == ctx['route_id'] and r['route_sha256'] == ctx['route_sha256'] and r['schema_sha256'] == ctx['schema_sha256'] and r['account_ref'] == ctx['account_ref'] for r in matches): continue
            arm = 'candidate' if int(digest({'task': task['key'], 'experiment': row['id']})[:8], 16) % 2 else 'baseline'
            version = plan[arm]
            value = {'experiment_id': row['id'], 'arm': arm, 'version': version, 'assigned_at': now, 'context_hash': plan['context_hash'],'task_key':task['key'],'run_id':run_id}
            if version != 'original': value['procedure'] = self.version(run_id, version)
            self.store.artifact(run_id, 0, key, value)
            return value
        # Adopted procedures remain scoped to the exact fresh adapter/schema.
        for route in matches:
            for row in self.db.execute('SELECT version FROM procedure_heads WHERE brand=?', (self.brand(run_id),)):
                if row['version'] == 'original': continue
                version = self.version(run_id, row['version']); ctx = version['context']
                if ctx['skill_id']==skill and all(route.get(k) == ctx.get(k) for k in ('route_id', 'route_sha256', 'schema_sha256', 'account_ref')):
                    value = {'experiment_id': None, 'arm': 'adopted', 'version': version['procedure_id'], 'procedure': version, 'assigned_at': now, 'context_hash': version['context_hash'],'task_key':task['key'],'run_id':run_id}
                    self.store.artifact(run_id, 0, key, value); return value
        return None

    def validate(self, run_id, ident):
        """Fresh assigned canaries prove compatibility only, not superiority."""
        version = self.version(run_id, ident); training = set(version['training_episodes']); valid = []
        for row in self.db.execute('SELECT id,body FROM execution_episodes WHERE brand=? AND context_hash=?', (self.brand(run_id), version['context_hash'])):
            episode = json.loads(row['body']); assignment = episode.get('assignment') or {}
            if row['id'] not in training and episode['source'] == version['source_class'] and episode['outcome'] == 'success' and (episode.get('started_at') or 0) >= version['created_at'] and assignment.get('version') == ident:
                valid.append(row['id'])
        result = {'procedure_id': ident, 'state': 'canary_verified' if valid else 'waiting_fresh_assigned_canary',
                  'new_execution_episodes': valid, 'superiority_verified': False, 'business_success_verified': False,
                  'fixture_only': version['source_class'] == 'controlled_fixture'}
        self.store.artifact(run_id, 0, 'procedure.validation.' + ident, result)
        return result

    def compare(self, run_id, ident):
        row = self.db.execute('SELECT body,result FROM procedure_experiments WHERE brand=? AND id=?', (self.brand(run_id), ident)).fetchone()
        if not row: raise ValueError('沒有本品牌執行實驗')
        if row['result']: return json.loads(row['result'])
        plan = json.loads(row['body']); reasons = []; groups = {'baseline': {}, 'candidate': {}}
        if self.store.clock() < plan['end_at']: reasons.append('observation_window_open')
        for item in self.db.execute("SELECT a.data FROM artifacts a JOIN runs r ON a.run_id=r.id WHERE a.kind LIKE 'procedure.assignment.%'"):
            assignment=json.loads(item[0])
            if assignment.get('experiment_id')==ident and assignment.get('arm') in groups:
                groups[assignment['arm']][assignment['task_key']]=[]
        for e in self.db.execute('SELECT id,body FROM execution_episodes WHERE brand=? AND context_hash=?', (self.brand(run_id), plan['context_hash'])):
            episode = json.loads(e['body']); assignment = episode.get('assignment') or {}; arm = assignment.get('arm')
            if assignment.get('experiment_id') != ident or arm not in groups: continue
            if episode['source'] != plan['source_class'] or episode.get('started_at') is None or not plan['start_at'] <= episode['started_at'] <= episode['finished_at'] <= plan['end_at']:
                reasons.append('assigned_episode_outside_fixed_context_or_window'); continue
            groups[arm].setdefault(episode['task_key'], []).append((e['id'], episode))
        values = {}; episode_ids = []
        for arm, tasks in groups.items():
            successes = 0; latencies = []
            if len(tasks) < plan['minimum_sample']: reasons.append(arm + ':insufficient_independent_tasks')
            for entries in tasks.values():
                if not entries:
                    reasons.append(arm+':assigned_task_without_execution');continue
                episode_ids.extend(e[0] for e in entries)
                # Retries stay in one unit; unresolved external writes fail.
                successes += int(any(e[1]['outcome'] == 'success' for e in entries) and not any(e[1]['operation_state'] in ('uncertain', 'in_flight') for e in entries))
                elapsed = [e[1].get('duration_ms') for e in entries]
                if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in elapsed): reasons.append(arm + ':unknown_execution_duration')
                else: latencies.append(sum(elapsed))
            for task_key in tasks:
                task=self.store.task(task_key)
                if not task:reasons.append(arm+':missing_original_task');continue
                for original in self.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'tool.attempt.%'",(task['run_id'],)):
                    attempt=json.loads(original[0])
                    if attempt['task_key']==task_key and attempt['state']=='pending' and attempt.get('route_sha256')==plan['context']['route_sha256']:
                        reasons.append(arm+':unresolved_tool_attempt')
            values[arm] = {'tasks': len(tasks), 'successes': successes, 'rate': successes/len(tasks) if tasks else None,
                          'mean_tool_duration_ms': sum(latencies)/len(latencies) if latencies else None}
        result = {'experiment_id': ident, 'family': 'execution', 'metric': plan['metric'], 'decision': 'wait',
            'reasons': sorted(set(reasons)), 'values': values, 'original_episode_ids': sorted(episode_ids),
            'uncertainty': None, 'causality_claim': False, 'business_success_claim': False,
            'fixture_only': plan['source_class'] == 'controlled_fixture', 'compared_at': self.store.clock()}
        if not reasons:
            old, new = values['baseline'], values['candidate']
            a, b = _wilson(old['successes'], old['tasks']), _wilson(new['successes'], new['tasks'])
            result['uncertainty'] = {'lower': b[0]-a[1], 'upper': b[1]-a[0], 'method': 'conservative_wilson_difference_95'}
            if old['mean_tool_duration_ms'] == 0 and new['mean_tool_duration_ms'] > 0 or old['mean_tool_duration_ms'] > 0 and new['mean_tool_duration_ms']/old['mean_tool_duration_ms'] > plan['maximum_latency_ratio']:
                result['decision'] = 'retain'; result['reasons'].append('latency_guardrail_regression')
            elif result['uncertainty']['lower'] > plan['minimum_improvement']:
                result['decision'] = 'adopt'
            elif result['uncertainty']['upper'] <= plan['minimum_improvement']:
                result['decision'] = 'retain'
            else:
                result['decision'] = 'inconclusive'; result['reasons'].append('effect_uncertain_at_fixed_horizon')
            result['evidence_digest'] = digest(sorted(episode_ids))
            self.db.execute('UPDATE procedure_experiments SET result=? WHERE brand=? AND id=?', (canonical(result), self.brand(run_id), ident))
        return result

    def adopt(self, run_id, ident):
        result = self.compare(run_id, ident)
        if result['decision'] != 'adopt': raise ValueError('執行證據不足以採用；成功一次不等於比較勝出')
        row = self.db.execute('SELECT body FROM procedure_experiments WHERE brand=? AND id=?', (self.brand(run_id), ident)).fetchone()
        plan = json.loads(row[0]); validation = self.validate(run_id, plan['candidate'])
        if validation['state'] != 'canary_verified': raise ValueError('仍缺新任務的候選方法實際驗證')
        with self.store.transaction():
            head = self.db.execute('SELECT version FROM procedure_heads WHERE brand=? AND context_hash=?', (self.brand(run_id), plan['context_hash'])).fetchone()
            if (head[0] if head else 'original') != plan['baseline']: raise ValueError('基準已改變，需重新比較')
            self.db.execute('INSERT INTO procedure_heads VALUES(?,?,?,?) ON CONFLICT(brand,context_hash) DO UPDATE SET version=excluded.version,previous=excluded.previous', (self.brand(run_id), plan['context_hash'], plan['candidate'], plan['baseline']))
            self.db.execute("UPDATE procedure_versions SET state='adopted' WHERE brand=? AND id=?", (self.brand(run_id), plan['candidate']))
            self.store.event(run_id, None, 'procedure_adopted', {'version': plan['candidate'], 'experiment': ident, 'business_success_claim': False})
        return {'procedure_id': plan['candidate'], 'state': 'adopted', 'permissions_unchanged': True}

    def rollback(self, run_id, ident, reason):
        version = self.version(run_id, ident)
        if not reason: raise ValueError('回退需具體原因')
        with self.store.transaction():
            row = self.db.execute('SELECT version,previous FROM procedure_heads WHERE brand=? AND context_hash=?', (self.brand(run_id), version['context_hash'])).fetchone()
            if not row or row['version'] != ident or not row['previous']: raise ValueError('此版本不是目前採用版或沒有前版')
            self.db.execute('UPDATE procedure_heads SET version=?,previous=NULL WHERE brand=? AND context_hash=?', (row['previous'], self.brand(run_id), version['context_hash']))
            self.db.execute("UPDATE procedure_versions SET state='rolled_back' WHERE brand=? AND id=?", (self.brand(run_id), ident))
            self.store.event(run_id, None, 'procedure_rollback', {'from': ident, 'to': row['previous'], 'reason': reason, 'existing_requests_preserved': True})
        return {'state': 'rolled_back', 'version': row['previous'], 'existing_requests_unchanged': True}
