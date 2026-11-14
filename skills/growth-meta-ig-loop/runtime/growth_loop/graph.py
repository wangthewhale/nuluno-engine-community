"""Versioned executable graphs layered over the durable v0.2 task contract."""
import json
from .contracts import blueprint
from .files import canonical, digest
from .errors import WorkflowError

VERSION = '1.0.0'


def compile_graph(flow):
    nodes, edges = [], []
    for stage in flow['stages']:
        for position, skill in enumerate(stage['skill_ids'], 1):
            ident = 'flow.%s.%02d.%02d.%s' % (flow['id'], stage['order'], position, skill)
            nodes.append({'id': ident, 'skill_id': skill, 'stage_order': stage['order'],
                          'stage_action': stage['action'], 'role': 'stage'})
    if 'S34' not in flow['stages'][-1]['skill_ids']:
        nodes.extend({'id': 'flow.feedback.initial.' + s, 'skill_id': s, 'stage_order': 999,
                      'stage_action': '取得新結果並依完整證據比較下一輪。', 'role': 'feedback'} for s in ('S33', 'S34'))
    for i, node in enumerate(nodes):
        target = nodes[i + 1]['id'] if i + 1 < len(nodes) else 'flow.finalize'
        edges.append({'from': node['id'], 'to': target, 'when': 'completed'})
        review = next((n['id'] for n in nodes[i + 1:] if n['skill_id'] == 'S33'), target)
        edges.append({'from': node['id'], 'to': review, 'when': 'no_results'})
    return {'schema': 'growth-graph-v1', 'id': flow['id'], 'version': VERSION,
            'nodes': nodes, 'edges': edges, 'wait': {'event': 'authenticated_matching_event', 'time': 'next_at'},
            'repair': {'same_task': True, 'requires': 'no_uncertain_operations', 'max_attempts': 5},
            'stop': {'iteration_limit': 'profile.workflow.max_iterations', 'owner_stop': True}}


def catalog():
    return [compile_graph(f) for f in blueprint()['workflows']]


class GraphRuntime:
    def __init__(self, store):
        self.store = store

    def pin(self, run_id, cycle, graph):
        old = self.store.artifact(run_id, cycle, 'graph.definition')
        if old and digest(old) != digest(graph):
            raise WorkflowError('執行中的graph不可原地換版。', 'payload_changed')
        self.store.artifact(run_id, cycle, 'graph.definition', graph)
        return digest(graph)

    def transition(self, task, result):
        graph = self.store.artifact(task['run_id'], task['cycle'], 'graph.definition')
        if not graph or graph.get('schema')!='growth-graph-v1':
            return  # v0.2 workspaces keep their original dependency semantics
        condition = 'no_results' if result.get('execution_level') == 'no_results' else 'completed'
        edge = next((e for e in graph['edges'] if e['from'] == task['kind'] and e['when'] == condition), None)
        if edge:
            self.store.event(task['run_id'], task['key'], 'graph_transition', {**edge, 'version': graph['version']})
        if condition == 'no_results' and edge:
            ids = [n['id'] for n in graph['nodes']]
            start, end = ids.index(task['kind']) + 1, ids.index(edge['to']) if edge['to'] in ids else len(ids)
            for kind in ids[start:end]:
                key = '%s:%s:%s' % (task['run_id'], task['cycle'], kind)
                self.store.db.execute("UPDATE tasks SET state='done',outcome=? WHERE key=? AND state='ready'", (canonical({'execution_level': 'branch_skipped', 'source_task': task['key'], 'edge': edge}), key))

    def stop(self, run_id, reason):
        if not reason:
            raise ValueError('停止需原因')
        with self.store.transaction():
            self.store.artifact(run_id, 0, 'graph.stopped', {'reason': reason, 'at': self.store.clock()})
            self.store.db.execute("UPDATE tasks SET state='cancelled',lease=NULL,lease_until=NULL WHERE run_id=? AND state NOT IN ('done','cancelled')", (run_id,))
            self.store.event(run_id, None, 'graph_stopped', {'reason': reason, 'uncertain_operations_preserved': True})
            from .continuity import finish
            finish(self.store,run_id,'owner_stop')

    def repair(self, run_id, task_key, reason):
        task = self.store.task(task_key)
        if not task or task['run_id'] != run_id or task['state'] in ('running', 'done', 'cancelled'):
            raise WorkflowError('只能修復本工作未完成且無租約的節點。', 'scope_mismatch')
        if self.store.artifact(run_id, 0, 'graph.stopped'):
            raise WorkflowError('已停止流程需要另建明確範圍。', 'scope_mismatch')
        pending = self.store.db.execute("SELECT 1 FROM operations WHERE task_key=? AND state IN ('in_flight','uncertain')", (task_key,)).fetchone()
        if pending:
            raise WorkflowError('先對帳不明外部操作，不能重送。', 'uncertain')
        if not reason or task['attempts'] >= self.store.config(run_id).get('recovery', {}).get('max_attempts', 5):
            raise WorkflowError('需修復原因且不可超出恢復上限。', 'scope_mismatch')
        self.store.db.execute("UPDATE tasks SET state='ready',next_at=0 WHERE key=?", (task_key,))
        self.store.event(run_id, task_key, 'graph_repair', {'reason': reason, 'original_id_preserved': True})
        return self.store.task(task_key)


def validate_graph(graph):
    if graph.get('schema') != 'growth-graph-v1' or not graph.get('version'):
        raise ValueError('graph版本與schema無效')
    base = next((g for g in catalog() if g['id'] == graph.get('id')), None)
    if not base or graph['nodes'] != base['nodes'] or graph.get('stop') != base['stop'] or graph.get('repair') != base['repair'] or graph.get('wait') != base['wait']:
        raise ValueError('候選graph須沿用工作節點、等待、安全停止與恢復契約')
    ids = [n['id'] for n in graph['nodes']] + ['flow.finalize']
    for edge in graph.get('edges', []):
        if set(edge) != {'from', 'to', 'when'} or edge['from'] not in ids[:-1] or edge['to'] not in ids or edge['when'] not in ('completed', 'no_results') or ids.index(edge['to']) <= ids.index(edge['from']):
            raise ValueError('候選分支需前向且條件明確；反覆由有界續輪處理')
        if edge['when'] == 'completed' and edge not in base['edges']:
            raise ValueError('不能省略必要完成步驟')
    if any(sum(e['from'] == node and e['when'] == condition for e in graph['edges']) != 1 for node in ids[:-1] for condition in ('completed','no_results')):
        raise ValueError('每個節點需唯一完成與無結果分支')
    return graph
