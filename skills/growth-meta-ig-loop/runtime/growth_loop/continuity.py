"""A durable goal can outlive one bounded attempt. Uses the ORIGINAL task queue."""
import json
import math
from .files import digest, canonical
from .errors import WorkflowError

VERSION = 'goal-continuity-1'


def scope(profile):
    return digest({'brand': profile['brand']['url'], 'goal': profile.get('goal'),
                   'bindings': profile.get('bindings', {}), 'workflow': profile.get('workflow', {}).get('id')})


def state(store, run_id):
    value = store.artifact(run_id, 0, 'goal.pursuit')
    return value if value and value.get('enabled') is True and value.get('version') == VERSION else None


def save(store, run_id, value):
    store.artifact(run_id, 0, 'goal.pursuit', value)
    return value


def configure(store, run_id, enabled=True, deadline_at=None):
    profile = store.config(run_id)
    if profile.get('workflow', {}).get('id') != 'goal' or not profile.get('goal', {}).get('id'):
        raise WorkflowError('持續模式需原本已登記的量化目標。', 'invalid_profile')
    if store.artifact(run_id, 0, 'graph.stopped'):
        raise WorkflowError('本人停止的工作不能由持續模式重開。', 'scope_mismatch')
    old = store.artifact(run_id, 0, 'goal.pursuit')
    if not enabled:
        if not old:return {'version':VERSION,'enabled':False,'state':'disabled'}
        value = {**(old or {}), 'version': VERSION, 'enabled': False, 'state': 'disabled'}
        for row in store.db.execute("SELECT key,data FROM tasks WHERE run_id=? AND state IN ('ready','retry')", (run_id,)).fetchall():
            if json.loads(row['data']).get('continuity'):
                store.db.execute("UPDATE tasks SET state='cancelled' WHERE key=?", (row['key'],))
        return save(store, run_id, value)
    if deadline_at is not None and (type(deadline_at) not in (int, float) or not math.isfinite(deadline_at) or deadline_at <= 0):
        raise ValueError('deadline_at需明確的原目標時間，不能延後原期限')
    if old and old.get('version') == VERSION:
        if old['scope_sha256'] != scope(profile):
            raise WorkflowError('目標、分母或品牌範圍已變更。', 'payload_changed')
        if deadline_at is not None and deadline_at != old.get('deadline_at'):
            raise WorkflowError('不能藉重新開啟延長原目標期限。', 'payload_changed')
        old['enabled'] = True
        return save(store, run_id, old)
    row = store.db.execute('SELECT max(cycle) FROM tasks WHERE run_id=?', (run_id,)).fetchone()
    value = {'version': VERSION, 'enabled': True, 'scope_sha256': scope(profile), 'registered_at': store.clock(),
             'state': 'open', 'goal_achieved': False, 'phase': 1, 'phase_start_cycle': max(1, row[0] or 1),
             'discovery_round': 0, 'deadline_at': deadline_at, 'diagnosed': {}, 'transitions': [],
             'budget_expanded': False, 'permissions_expanded': False}
    terminal = store.artifact(run_id, 0, 'goal.latest_terminal') or {}
    if terminal.get('decision') in ('target_reached', 'stop_guardrail'):
        value['state'] = 'achieved' if terminal['decision'] == 'target_reached' else 'awaiting_owner'
        value['goal_achieved'] = terminal['decision'] == 'target_reached'
    return save(store, run_id, value)


def check_scope(store, run_id):
    p = state(store, run_id)
    if p and p['scope_sha256'] != scope(store.config(run_id)):
        raise WorkflowError('原目標範圍變更，保留原工作等待核對。', 'payload_changed')
    return p


def transition(store, run_id, kind, reason, **details):
    p = check_scope(store, run_id)
    if not p:
        return
    p.update(state=kind, last_reason=reason, last_transition_at=store.clock())
    p['transitions'].append({'state': kind, 'reason': reason, 'at': store.clock(), **details})
    save(store, run_id, p)
    store.event(run_id, None, 'goal_continuity_' + kind, {'reason': reason, **details})


def phase_cycle(store, run_id, cycle):
    return cycle - (state(store, run_id) or {}).get('phase_start_cycle', 1) + 1


def new_phase(store, run_id, cycle):
    p = check_scope(store, run_id)
    if not p:
        return
    p['phase'] += 1
    p['phase_start_cycle'] = cycle
    save(store, run_id, p)
    transition(store, run_id, 'working', 'distinct_evidenced_hypothesis', phase=p['phase'], cycle=cycle)


def discover(store, task, reason, blocked_task=None):
    run_id = task['run_id']
    p = check_scope(store, run_id)
    if not p or p.get('goal_achieved') or store.artifact(run_id, 0, 'graph.stopped'):
        return False
    if (store.artifact(run_id, 0, 'goal.latest_terminal') or {}).get('decision') in ('target_reached', 'stop_guardrail'):
        return False
    if p.get('deadline_at') and store.clock() >= p['deadline_at']:
        transition(store, run_id, 'window_missed', 'original_goal_deadline_passed')
        return False
    if store.db.execute("SELECT 1 FROM operations WHERE task_key IN (SELECT key FROM tasks WHERE run_id=?) AND state IN ('in_flight','uncertain')", (run_id,)).fetchone():
        transition(store, run_id, 'awaiting_reconciliation', 'original_operation_unconfirmed')
        return False
    for row in store.db.execute("SELECT key,data,depends FROM tasks WHERE run_id=? AND state IN ('ready','retry','running','waiting_agent')", (run_id,)):
        if row['key'] != task['key'] and json.loads(row['data']).get('continuity') and all((store.task(k) or {}).get('state') == 'done' or k == task['key'] for k in json.loads(row['depends'])):
            return False
    for row in store.db.execute("SELECT key,data FROM tasks WHERE run_id=? AND state='failed'", (run_id,)).fetchall():
        data = json.loads(row['data'])
        if data.get('continuity') or data.get('role') == 'goal_advance':
            store.db.execute("UPDATE tasks SET state='cancelled' WHERE key=? AND NOT EXISTS (SELECT 1 FROM operations WHERE task_key=?)", (row['key'], row['key']))
    p['discovery_round'] += 1
    generation = p['discovery_round']
    save(store, run_id, p)
    profile = store.config(run_id)
    delay = min(86400, max(60, profile['goal'].get('check_cadence_seconds', 3600)) * 2 ** min(generation - 1, 8))
    from .goals import GoalRunner
    runner = GoalRunner(store)
    dep = [] if blocked_task else [task['key']]
    research = runner.enqueue_skill(run_id, task['cycle'], 'continuity', 'research.' + str(generation), 'S03', dep, delay=delay)
    review = runner.enqueue_skill(run_id, task['cycle'], 'continuity', 'review.' + str(generation), 'S34', [research])
    advance = store.enqueue(run_id, task['cycle'], 'flow.goal.continuity.advance.' + str(generation), {'role': 'goal_advance'}, [review])
    nodes = [research, review, advance]
    for key in nodes:
        data = store.task(key)['data']
        data['continuity'] = {'round': generation, 'reason': reason, 'blocked_task': blocked_task}
        store.db.execute('UPDATE tasks SET data=? WHERE key=?', (canonical(data), key))
    extension = {'schema': 'growth-graph-extension-v1', 'parent_sha256': digest(store.artifact(run_id, task['cycle'], 'graph.definition')),
                 'nodes': nodes, 'edges': [{'from': a, 'to': b} for a, b in zip(nodes, nodes[1:])],
                 'original_goal_and_permissions_unchanged': True}
    store.artifact(run_id, task['cycle'], 'graph.continuity.' + str(generation), extension)
    transition(store, run_id, 'discovering', reason, tasks=nodes, next_at=store.clock() + delay)
    return True


def finish(store, run_id, decision):
    p = state(store, run_id)
    if p:
        p['goal_achieved'] = decision == 'target_reached'
        save(store, run_id, p)
        transition(store, run_id, 'achieved' if p['goal_achieved'] else 'suspended', decision)
        if p['goal_achieved']:
            store.db.execute("UPDATE tasks SET state='cancelled' WHERE run_id=? AND state IN ('ready','retry','waiting_agent')", (run_id,))


def before(store, task):
    p = check_scope(store, task['run_id'])
    if p and p.get('goal_achieved'):
        from .errors import Waiting
        raise Waiting('原始證據已確認達標；不再新增測試。', 'waiting_owner')
    if p and p.get('deadline_at') and store.clock() >= p['deadline_at'] and not task['data'].get('measurement_role') and not task['data'].get('expiry_guard') and task['data'].get('role') != 'goal_evaluate':
        transition(store, task['run_id'], 'window_missed', 'original_goal_deadline_passed')
        from .errors import Waiting
        raise Waiting('原期限已過；保留原結果，不能延後期限繼續寫入。', 'waiting_owner')


def repair_preparation(store, run_id, task_key, instruction, sources):
    """A new read-only successor, not a reset of the failed request or writes."""
    from .contracts import ALLOWED_ACTIONS
    p = check_scope(store, run_id)
    old = store.task(task_key)
    if not p or not old or old['run_id'] != run_id or old['state'] != 'failed' or not old['data'].get('skill_id') or ALLOWED_ACTIONS.get(old['data']['skill_id']):
        raise WorkflowError('只能替換失敗、沒有外部寫入的準備節點。', 'invalid_result')
    from .channels import channel_skill
    specific = old['data'].get('channel_skill_id')
    if specific and channel_skill(specific)['allowed_actions']:
        raise WorkflowError('平台操作不能由文字提案重送。', 'invalid_result')
    if store.db.execute("SELECT 1 FROM operations WHERE task_key=? OR (state IN ('in_flight','uncertain') AND task_key IN (SELECT key FROM tasks WHERE run_id=?))", (task_key, run_id)).fetchone():
        raise WorkflowError('原操作需先對帳，不能重送。', 'uncertain')
    if not isinstance(instruction, str) or not 12 <= len(instruction.strip()) <= 2000 or not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise WorkflowError('修正需具體步驟及已保存的原始來源。', 'invalid_result')
    known = set()
    for row in store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'flow_output.%'", (run_id,)):
        known.update(e.get('reference') for e in json.loads(row[0]).get('evidence', []) if isinstance(e, dict))
    if not set(sources).issubset(known):
        raise WorkflowError('修正來源必須存在於完成的原始輸出。', 'invalid_result')
    original = old['data'].get('continuity_repair', {}).get('original_task', task_key)
    fingerprint = digest({'original': original, 'instruction': ' '.join(instruction.split()).casefold()})
    if fingerprint in p['diagnosed']:
        raise WorkflowError('同一修正已嘗試，需新證據及不同方法。', 'invalid_result')
    data = {**old['data'], 'continuity_repair': {'from': task_key, 'original_task': original, 'instruction': instruction, 'sources': sources}}
    successor = store.enqueue(run_id, old['cycle'], old['kind'] + '.repair.' + str(p['discovery_round']), data, old['depends'])
    for row in store.db.execute("SELECT key,depends FROM tasks WHERE run_id=? AND state NOT IN ('done','cancelled') AND key<>?", (run_id, task_key)).fetchall():
        depends = json.loads(row['depends'])
        if task_key in depends:
            store.db.execute('UPDATE tasks SET depends=? WHERE key=?', (canonical([successor if d == task_key else d for d in depends]), row['key']))
    store.db.execute("UPDATE tasks SET state='cancelled' WHERE key=?", (task_key,))
    p['diagnosed'][fingerprint] = successor
    save(store, run_id, p)
    transition(store, run_id, 'working', 'failed_preparation_replaced', original_task=task_key, successor_task=successor)
    return successor


def observe_failure(store, task):
    p = state(store, task['run_id'])
    if not p:
        return
    t = store.task(task['key'])
    outcome = t.get('outcome') or {}
    kind = outcome.get('kind', t['state'])
    if t['state'].startswith('waiting_'):
        transition(store, t['run_id'], t['state'], kind, task_key=t['key'])
        return
    if t['state'] != 'failed' or not t['data'].get('skill_id') and t['data'].get('role') != 'goal_advance':
        return
    from .contracts import ALLOWED_ACTIONS
    if ALLOWED_ACTIONS.get(t['data'].get('skill_id')) or kind in ('scope_mismatch', 'payload_changed', 'uncertain', 'auth_expired', 'new_goal_required'):
        transition(store, t['run_id'], 'awaiting_operator', kind, task_key=t['key'])
        return
    marker = digest({'task': t['key'], 'kind': kind})
    if marker not in p['diagnosed']:
        p['diagnosed'][marker] = True
        save(store, t['run_id'], p)
        discover(store, t, kind, blocked_task=t['key'])


def summary(store, run_id):
    p = store.artifact(run_id, 0, 'goal.pursuit')
    if not p:
        return None
    tasks = [store.task(row[0]) for row in store.db.execute('SELECT key FROM tasks WHERE run_id=?', (run_id,))]
    eligible = [t for t in tasks if t['state'] in ('ready', 'retry', 'running') and all((store.task(k) or {}).get('state') == 'done' for k in t['depends'])]
    executing = any(t['state'] == 'running' and (t.get('lease_until') or 0) > store.clock() for t in tasks)
    return {k: p.get(k) for k in ('version', 'enabled', 'state', 'goal_achieved', 'phase', 'discovery_round', 'deadline_at', 'last_reason')} | {
        'currently_executing': executing, 'next_at': min((t['next_at'] for t in eligible), default=None),
        'queued_work': [{'task_key': t['key'], 'state': t['state'], 'next_at': t['next_at']} for t in eligible],
        'blocking_tasks': [{'task_key': t['key'], 'state': t['state'], 'reason': (t.get('outcome') or {}).get('reason')} for t in tasks if t['state'].startswith('waiting_') or t['state'] == 'failed'],
        'requires_actual_trusted_worker': True, 'permission_or_budget_expansion': False}
