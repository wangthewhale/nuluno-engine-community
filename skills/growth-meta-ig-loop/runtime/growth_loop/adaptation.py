"""Constrained plan composition; the durable executor retains every permission gate."""
import json
from collections import Counter
from contextlib import nullcontext
from .files import digest
from .errors import WorkflowError
from .capabilities import CapabilityBroker


def planning_scope(profile):
    value=json.loads(json.dumps(profile))
    value.get('brand',{}).pop('facts',None)  # Expected S01 outputs, not new authority.
    return digest(value)


def spec(ident):
    from .channels import channel_skill
    from .contracts import FIELDS, ALLOWED_ACTIONS
    if ident.startswith('S'):
        if ident not in FIELDS:raise ValueError('Unknown core skill: '+str(ident))
        return {'id': ident, 'parent_skill_id': ident, 'channel': 'local',
                'allowed_actions': sorted(ALLOWED_ACTIONS.get(ident, []))}
    return channel_skill(ident)


def sequences_for(goal, setting):
    from .goals import goal_definition
    if goal.get('branches'):
        return {key: list(value) for key, value in goal['branches'].items()
                if key in setting.get('sources', list(goal['branches']))}
    if goal['channel'] == 'audience' and setting.get('execution_goal_id'):
        target = goal_definition(setting['execution_goal_id'])
        return {target['channel']: list(goal['skills'][:-1]) + target['skills']}
    return {goal['channel']: list(goal['skills'])}


def validate_sequences(goal, setting, sequences):
    """Known skills only; same branches, mandatory work, action families and metric."""
    original = sequences_for(goal, setting)
    if not isinstance(sequences, dict) or set(sequences) != set(original):
        raise ValueError('組合流程須保留原目標的渠道與分支；換渠道另建目標')
    for branch, old in original.items():
        new = sequences[branch]
        if not isinstance(new, list) or not 2 <= len(new) <= 48 or any(not isinstance(s, str) for s in new):
            raise ValueError('每個分支需2至48個已知技能')
        if any(count>max(Counter(old)[ident],1) for ident,count in Counter(new).items()) or new[-1] != old[-1]:
            raise ValueError('不可重複動作或更換最後的原生量測技能')
        before, after = [spec(s) for s in old], [spec(s) for s in new]
        permitted_channels={branch,'local','audience'}|{s['channel'] for s in before}
        if any(s['channel'] not in permitted_channels for s in after):
            raise ValueError('不能把其他渠道的技能放進本分支')
        old_parents = {s['parent_skill_id'] for s in before}
        new_parents = {s['parent_skill_id'] for s in after}
        if not (old_parents - {'S12', 'S13'}).issubset(new_parents):
            raise ValueError('缺少原目標必要的研究、文案、操作或量測')
        actions = lambda items: {a for s in items if s['parent_skill_id'] != 'S05' for a in s['allowed_actions']}
        if actions(before) != actions(after):
            raise ValueError('組合不能新增或略過原目標的對外動作類型')
        families=lambda items:Counter(tuple(sorted(s['allowed_actions'])) for s in items if s['allowed_actions'] and s['parent_skill_id']!='S05')
        if families(before)!=families(after):
            raise ValueError('組合不能增加對外動作次數或略過必要動作')
        # Native read/write variants may replace one another only within the
        # same parent operation. Essential account/research/measurement stays.
        for item in before:
            if item['parent_skill_id'] not in ('S12', 'S13', 'S14') and item['id'] not in new:
                raise ValueError('原目標的具體渠道操作不可省略：' + item['id'])
        if any(s['allowed_actions'] and s['id'].startswith('S') and s['id'] not in old for s in after):
            raise ValueError('不得用泛用寫入技能繞過具體渠道契約')
        access = [i for i, s in enumerate(after) if s['parent_skill_id'] == 'S05']
        writes = [i for i, s in enumerate(after) if s['allowed_actions'] and s['parent_skill_id'] != 'S05']
        if access and writes and max(access) > min(writes):
            raise ValueError('帳號驗證必須先於對外操作')
        # Preserve the original relative order of all mandatory original work.
        retained = [s for s in old if s in new]
        cursor=0
        for ident in retained:
            try:cursor=new.index(ident,cursor)+1
            except ValueError:raise ValueError('必要原始步驟的因果順序不可倒置') from None
        for i, item in enumerate(after):
            if item['parent_skill_id'] == 'S14' and item['allowed_actions']:
                media = 'S13' if item['channel'] in ('youtube','tiktok') or 'REEL' in item['id'] else ('S12' if item['channel']=='pinterest' or 'CAROUSEL' in item['id'] else None)
                if media and media not in new[:i]:
                    raise ValueError('發布格式需要先產生對應的實際素材：' + media)
        if 'completed_views' in goal['metric']['components'] and 'S13' not in new:
            raise ValueError('影片完成率目標不可被換成圖片格式')
        if {s['id'] for s in before if s['parent_skill_id']=='S14'}=={s['id'] for s in after if s['parent_skill_id']=='S14'} and not (old_parents&{'S12','S13'}).issubset(new_parents):
            raise ValueError('發布格式未變，不能略過原本的素材製作')
    return sequences


class AdaptivePlanner:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.broker = CapabilityBroker(store, config)

    def compose(self, run_id, cycle, proposal=None):
        from .goals import goal_definition, compile_goal
        from .execution import required_capabilities
        if type(cycle) is not int or cycle < 1:
            raise ValueError('輪次需正整數')
        profile = self.store.config(run_id)
        if profile.get('workflow', {}).get('id') != 'goal':
            raise ValueError('組合器需要一個已定義的具體目標')
        from .continuity import phase_cycle
        if phase_cycle(self.store,run_id,cycle) > profile['workflow'].get('max_iterations', 1):
            raise ValueError('不能超出原目標的有界輪數')
        goal = goal_definition(profile['goal']['id']); inventory = self.broker.inventory(run_id)
        sequences = sequences_for(goal, profile['goal']); sources = ['event:capability.inventory.' + inventory['inventory_sha256']]
        reason = '依目前工具選擇可驗證的同渠道格式；缺口保留為等待，不假裝有工具'
        if proposal is not None:
            if not isinstance(proposal, dict) or set(proposal) != {'sequences', 'reason', 'sources'} or not proposal['reason'] or not proposal['sources']:
                raise ValueError('模型提案需明確sequences、理由與來源；不可附加權限或程式碼')
            sequences = proposal['sequences']; reason = proposal['reason']; sources = proposal['sources']
        else:
            from .channels import channel_catalog
            for branch, sequence in sequences.items():
                for index, ident in enumerate(list(sequence)):
                    item = spec(ident)
                    if item['parent_skill_id'] != 'S14' or self.broker.select(run_id, ident):
                        continue
                    alternatives = [s for s in channel_catalog()['channel_skills'] if s['channel'] == branch
                        and s['parent_skill_id'] == 'S14' and set(s['allowed_actions']) == set(item['allowed_actions'])
                        and self.broker.select(run_id, s['id'])]
                    if alternatives and 'completed_views' not in goal['metric']['components']:
                        chosen = sorted(alternatives, key=lambda s: s['id'])[0]['id']
                        sequence[index] = chosen
                        if 'REEL' in chosen and 'S12' in sequence: sequence[sequence.index('S12')] = 'S13'
        validate_sequences(goal, profile['goal'], sequences)
        graph = compile_goal(goal, profile['goal'], cycle, sequences_override=sequences)
        blocked = []
        for node in graph['nodes']:
            if not node.get('skill'): continue
            item = spec(node['skill']); parent = item['parent_skill_id']
            routes = self.broker.select(run_id, node['skill'])
            caps = required_capabilities(parent, profile)
            if not node['skill'].startswith('S'): caps = list(dict.fromkeys(caps + [item['capability']]))
            available = {cap for r in routes for cap in r['capabilities']}
            local = {cap for cap in inventory['host_capabilities'] if cap.startswith(('agent.', 'local.'))}
            missing = sorted(set(caps) - available - local)
            node['execution_options'] = [r['route_id'] for r in routes]
            node['missing_capabilities'] = missing
            if missing: blocked.append({'node': node['id'], 'skill_id': node['skill'], 'missing': missing})
        body = {'schema': 'growth-adaptive-plan-v1', 'run_id': run_id, 'cycle': cycle,
                'brand_url': profile['brand']['url'], 'profile_sha256': planning_scope(profile),
                'profile_binding':'all_fields_except_observed_brand_facts',
                'inventory_sha256': inventory['inventory_sha256'], 'graph': graph,
                'sequences': sequences, 'reason': reason, 'sources': sources,
                'blocked_nodes': blocked, 'permissions_unchanged': True}
        ident = digest(body)
        self.store.artifact(run_id, cycle, 'adaptive.plan.' + ident, body)
        return {'plan_id': ident, **body, 'ready_without_missing_tools': not blocked}

    def apply(self, run_id, plan_id):
        row = self.store.db.execute("SELECT cycle,data FROM artifacts WHERE run_id=? AND kind=?", (run_id, 'adaptive.plan.' + plan_id)).fetchone()
        if not row: raise ValueError('沒有本工作的候選流程')
        plan = json.loads(row['data']); cycle = row['cycle']
        with (nullcontext() if self.store.db.in_transaction else self.store.transaction()):
            old=self.store.artifact(run_id,cycle,'adaptive.applied')
            graph=self.store.artifact(run_id,cycle,'graph.definition')
            if old and old['plan_id']==plan_id and graph and digest(graph)==digest(plan['graph']):
                return {'plan_id':plan_id,'cycle':cycle,'state':'already_applied','already_applied':True,'permissions_unchanged':True}
            if self.store.artifact(run_id, 0, 'graph.stopped'):
                raise WorkflowError('原工作已停止；不能藉組合器恢復執行。', 'scope_mismatch')
            if self.store.artifact(run_id, cycle, 'graph.definition') or self.store.db.execute('SELECT 1 FROM tasks WHERE run_id=? AND cycle=?', (run_id, cycle)).fetchone():
                raise WorkflowError('只能套用到尚未建立的輪次；既有graph與外部操作不改寫。', 'payload_changed')
            if planning_scope(self.store.config(run_id)) != plan['profile_sha256'] or self.broker.inventory(run_id)['inventory_sha256'] != plan['inventory_sha256']:
                raise WorkflowError('現況已變更，需以新工具／資料重新組合。', 'payload_changed')
            if old and old['plan_id'] != plan_id:
                self.store.artifact(run_id,cycle,'adaptive.superseded.'+old['plan_id'],old)
                self.store.event(run_id,None,'unstarted_plan_superseded',{'from':old['plan_id'],'to':plan_id,'reason':plan['reason']})
            self.store.artifact(run_id, cycle, 'adaptive.applied', {'plan_id': plan_id, 'plan': plan, 'applied_at': self.store.clock()})
        return {'plan_id': plan_id, 'cycle': cycle, 'state': 'applied_before_execution', 'permissions_unchanged': True}
