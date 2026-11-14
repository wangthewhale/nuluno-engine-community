"""Buyer-outcome graphs, real metric gates and bounded, evidenced next work."""
import json
import math
from pathlib import Path
from .channels import channel_skill, channel_catalog
from .contracts import definition, _proof, ALLOWED_ACTIONS
from .errors import Waiting, WorkflowError
from .files import canonical,digest

from .resources import skill_root
REF=skill_root()/'references'

def goal_catalog():return json.loads((REF/'goal-catalog.json').read_text(encoding='utf-8'))
def goal_definition(ident):
    found=next((g for g in goal_catalog()['goals'] if g['id']==ident),None)
    if not found:raise ValueError('Unknown goal_id: '+str(ident))
    return found

def validate_goal_profile(profile):
    setting=profile.get('goal',{})
    goal=goal_definition(setting.get('id'))
    target=setting.get('target',{})
    if target.get('type') not in ('absolute_change','relative_percent','threshold') or type(target.get('value')) not in (int,float) or not math.isfinite(target['value']) or target['value']<0:raise ValueError('目標需明確的絕對變動、相對百分比或門檻與非負數值')
    if type(setting.get('minimum_sample')) is not int or setting['minimum_sample']<1:raise ValueError('需事前指定正整數樣本門檻，不套假定通用成功數字')
    from .execution import measurement_contract
    unit=measurement_contract(goal)['sample_unit']
    if setting.get('sample_unit',unit)!=unit:raise ValueError('此目標樣本單位固定為'+unit+'；不同定義需另建目標，不能原地換分母')
    if profile.get('mode')=='live' and unit=='account_snapshots' and setting['minimum_sample']!=1:raise ValueError('單一帳號每期是一個快照，minimum_sample需為1；追蹤者總數不是獨立樣本數')
    if profile.get('mode')=='live' and any(c.startswith('qualified_') for c in goal['metric']['components']) and not isinstance(setting.get('qualification_rule'),str):
        raise ValueError('合格成效需先登記本品牌qualification_rule，不以任何留言冒充合格買方')
    if profile.get('mode')=='live' and 'qualification_rule' in setting and not setting['qualification_rule'].strip():raise ValueError('qualification_rule不能留空')
    seconds=setting.get('observation_seconds',goal['observation_seconds'])
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<0 or (seconds==0 and profile.get('mode')!='fixture'):raise ValueError('需有效觀察窗口；只有fixture可零等待')
    if goal['channel']=='audience':
        if not setting.get('execution_goal_id'):raise ValueError('受眾測試需先指定實際發布／聯絡的execution_goal_id')
        execution=goal_definition(setting['execution_goal_id'])
        if execution['channel'] in ('audience','multi'):raise ValueError('受眾角度測試需選一個具體執行渠道目標')
    branches=setting.get('sources',list(goal.get('branches',{})))
    if goal.get('branches') and (not branches or set(branches)-set(goal['branches'])):raise ValueError('公開需求來源需選已支援的渠道')
    for guard in setting.get('guardrails',[]):
        if not isinstance(guard,dict) or not guard.get('metric') or guard.get('direction') not in ('maximum','minimum') or type(guard.get('threshold')) not in (int,float) or not math.isfinite(guard['threshold']):raise ValueError('品質限制需事前指標、maximum／minimum與有限門檻')
    if type(setting.get('max_stagnant_cycles',3)) is not int or not 2<=setting.get('max_stagnant_cycles',3)<=20:raise ValueError('無進展輪數上限需為2至20')
    return profile


def compile_goal(goal,setting=None,cycle=1,restart=None,sequences_override=None):
    setting=setting or {}
    sequences=goal.get('branches')
    if sequences:
        sequences={key:sequences[key] for key in setting.get('sources',list(sequences))}
    else:
        steps=list(goal['skills'])
        execution_channel=goal['channel']
        if goal['channel']=='audience' and setting.get('execution_goal_id'):
            execution=goal_definition(setting['execution_goal_id'])
            steps=steps[:-1]+execution['skills']
            execution_channel=execution['channel']
        sequences={execution_channel:steps}
    if sequences_override is not None:
        from .adaptation import validate_sequences
        sequences=validate_sequences(goal,setting,sequences_override)
    nodes=[];edges=[];common=[]
    if cycle==1:
        initial=('S01','S02') if goal['channel']=='audience' else ('S01','A-VOC','A-SEGMENTS','A-MOTIVES','A-PRIORITY','A-LADDER','A-ANGLES')
        for ident in initial:
            kind='flow.goal.common.'+ident
            nodes.append({'id':kind,'skill':ident,'branch':'common','depends':common,'role':'goal_common'})
            common=[kind]
    terminals=[]
    for branch,steps in sequences.items():
        previous=list(common);measurement=steps[-1];baseline=False;ordered=list(steps[:-1])
        if restart and restart not in ordered:
            if restart not in goal['bottleneck_routes'].values():raise ValueError('下一輪只能回本目標允許卡點')
            extra=channel_skill(restart) if not restart.startswith('S') else None
            if not extra or extra['channel'] in (branch,'audience'):ordered.insert(0,restart)
        def add(tag,ident,measurement_role=None):
            kind='flow.goal.%s.%s.%s'%(branch,tag,ident)
            node={'id':kind,'skill':ident,'branch':branch,'depends':list(previous),'role':'goal_stage','measurement_role':measurement_role}
            nodes.append(node)
            return [kind]
        for index,ident in enumerate(ordered):
            item=channel_skill(ident) if not ident.startswith('S') else None
            writes=item['allowed_actions'] if item else ALLOWED_ACTIONS.get(ident,set())
            if cycle==1 and not baseline and writes and (item['parent_skill_id'] if item else ident)!='S05':
                previous=add('baseline',measurement,'baseline');baseline=True
            previous=add('%02d'%index,ident)
        if cycle==1 and not baseline:previous=add('baseline',measurement,'baseline')
        previous=add('observe.0',measurement,'current')
        for ident in goal.get('after_measurement',[]):previous=add('finish',ident)
        terminals.extend(previous)
    nodes.append({'id':'flow.goal.evaluate.0','branch':'control','depends':terminals,'role':'goal_evaluate','check':0})
    for node in nodes:
        for dep in node['depends']:edges.append({'from':dep,'to':node['id'],'when':'completed'})
        if node.get('skill'):
            edges.extend({'from':node['id'],'to':target,'when':condition} for condition,target in (
              ('no_results','branch_observation'),('login_required','wait_owner'),('uncertain_write','reconcile_same_operation'),('rate_limited','wait_until_reset')))
    return {'schema':'growth-goal-graph-v1','id':goal['id'],'version':'1.1.0','nodes':nodes,'edges':edges,'branches':list(sequences),
       'baseline':'pinned before first external action per branch','metric':goal['metric'],'bottleneck_routes':goal['bottleneck_routes'],
       'controls':{'wait_owner':'original task, no new identity/permission','reconcile_same_operation':'uncertain writes cannot repeat','wait_until_reset':'durable next_at','branch_observation':'skip irrelevant branch actions, keep other channels running'},
       'loop_edges':[{'when':'data_incomplete','to':'observe_again_without_republishing'},{'when':'target_reached','to':'stop_success'},{'when':'target_not_reached','to':'strategy_review'},{'when':'strategy_iterate','to':'one_variable_next_test'},{'when':'quality_guardrail_violated','to':'stop_test_and_preserve_control'},{'when':'repeated_change_or_stagnation','to':'fresh_evidence_if_owner_enabled_continuity_else_stop'},{'when':'iteration_limit','to':'fresh_evidence_if_owner_enabled_continuity_else_stop'},{'when':'budget_limit','to':'await_original_budget_without_expansion'}]}


class GoalRunner:
    def __init__(self,store):self.store=store
    def seed(self,run_id,cycle=1,change=None,restart=None,delay=0):
        p=self.store.config(run_id);validate_goal_profile(p)
        if cycle==1 and p.get('pursuit',{}).get('enabled') is True and self.store.artifact(run_id,0,'goal.pursuit') is None:
            from .continuity import configure
            configure(self.store,run_id,True,p['pursuit'].get('deadline_at'))
        goal=goal_definition(p['goal']['id']);setting=p['goal']
        old=self.store.artifact(run_id,0,'goal.spec')
        if old and digest(old['setting'])!=digest(setting):raise WorkflowError('不能在原測試中悄悄修改目標、窗口或分母。','payload_changed')
        if not old:self.store.artifact(run_id,0,'goal.spec',{'definition':goal,'setting':setting,'registered_at':self.store.clock()})
        applied=self.store.artifact(run_id,cycle,'adaptive.applied')
        if applied:
            plan=applied['plan']
            from .adaptation import planning_scope
            if plan['profile_sha256']!=planning_scope(p):raise WorkflowError('組合後的品牌／目標設定已變更，需新提案。','payload_changed')
            graph=plan['graph']
        else:graph=compile_goal(goal,setting,cycle,restart)
        from .graph import GraphRuntime
        GraphRuntime(self.store).pin(run_id,cycle,graph)
        self.store.artifact(run_id,cycle,'goal.change',change or {'status':'baseline_test','hypothesis':'initial brand-specific goal experiment'})
        for node in graph['nodes']:
            dependencies=['%s:%s:%s'%(run_id,cycle,key) for key in node['depends']]
            if node['role']=='goal_evaluate':
                self.store.enqueue(run_id,cycle,node['id'],{'role':'goal_evaluate','check':0},dependencies)
                continue
            self.enqueue_skill(run_id,cycle,node['branch'],'',node['skill'],dependencies,node.get('measurement_role'),delay,node_kind=node['id'],role=node['role'])

    def enqueue_skill(self,run_id,cycle,branch,tag,ident,depends,measurement_role=None,delay=0,node_kind=None,role="goal_stage"):
        item=channel_skill(ident) if not ident.startswith('S') else None
        parent=item['parent_skill_id'] if item else ident
        data={'skill_id':parent,'channel_skill_id':item['id'] if item else None,'channel':item['channel'] if item else branch,
              'branch':branch,'stage_order':0,'stage_action':item['title'] if item else definition(ident)['responsibility'],
              'role':role,'measurement_role':measurement_role,'measurement_skill':ident}
        return self.store.enqueue(run_id,cycle,node_kind or 'flow.goal.%s.%s.%s'%(branch,tag,ident),data,depends,self.store.clock()+delay)

    def before(self,task,profile):
        if task['data'].get('measurement_role')!='current':return
        if self.store.artifact(task['run_id'],task['cycle'],'goal.empty_branch.'+task['data']['branch']):return
        seconds=profile['goal'].get('observation_seconds',goal_definition(profile['goal']['id'])['observation_seconds'])
        anchor_key='goal.intervention_anchor.'+task['data']['branch']
        anchor=self.store.artifact(task['run_id'],task['cycle'],anchor_key)
        if anchor is None:
            since=max([self.store.task(k)['updated'] for k in task['depends']] or [self.store.clock()])
            self.store.artifact(task['run_id'],task['cycle'],anchor_key,{'at':since,'source_task':task['key']})
        else:since=anchor['at']
        due=since+seconds
        if self.store.clock()<due:raise Waiting('本目標觀察窗尚未結束；不提早改稿或重新發文。','waiting_time',{'resume_at':due})

    def check_observation(self,task,profile,data):
        if not task['data'].get('measurement_role'):return
        goal=goal_definition(profile['goal']['id']);obs=data.get('goal_observation')
        if not isinstance(obs,dict) or obs.get('metric')!=goal['metric']['key']:
            raise WorkflowError('觀測需對應本目標的具體指標。','invalid_result')
        if obs.get('account_ref')!=profile.get('bindings',{}).get(task['data']['channel'],{}).get('account_ref') or not obs.get('account_ref'):
            raise WorkflowError('目標觀測需本品牌所選帳號。','scope_mismatch')
        window=obs.get('window',{})
        if any(type(window.get(k)) not in (int,float) or not math.isfinite(window[k]) for k in ('start','end')) or window['end']<window['start'] or window['end']>self.store.clock()+300:
            raise WorkflowError('觀測窗口無效。','invalid_result')
        if not obs.get('sources') or any(not isinstance(s,dict) or not _proof(s.get('reference'),profile.get('mode','prepare')) for s in obs['sources']):
            raise WorkflowError('目標觀測需可核對原始來源。','invalid_result')
        if profile.get('mode')=='live':
            if obs.get('run_id')!=task['run_id'] or obs.get('cycle')!=task['cycle'] or obs.get('role')!=task['data']['measurement_role']:
                raise WorkflowError('真實觀測需綁定原工作、輪次與基準／當期角色。','scope_mismatch')
            from .execution import measurement_contract
            sample_unit=profile['goal'].get('sample_unit',measurement_contract(goal)['sample_unit'])
            if obs.get('sample_unit')!=sample_unit:
                raise WorkflowError('樣本單位需對應本目標；追蹤人數不能冒充獨立帳號快照數。','invalid_result')
            max_age=profile.get('execution',{}).get('source_max_age_seconds',3600)
            if type(max_age) not in (int,float) or not math.isfinite(max_age) or not 0<max_age<=86400:
                raise WorkflowError('來源讀回有效期需在0至86400秒內。','invalid_input')
            for source in obs['sources']:
                stamp=source.get('observed_at')
                if type(stamp) not in (int,float) or not math.isfinite(stamp) or not max(self.store.clock()-max_age,window['end']-300)<=stamp<=self.store.clock()+300:
                    raise WorkflowError('真實觀測需本次來源讀回時間；不能用舊來源冒充新成績。','invalid_result')
        if obs.get('source_class')!=('controlled_fixture' if profile.get('mode')=='fixture' else 'live_tool'):
            raise WorkflowError('測試／真實目標觀測不能混用。','invalid_result')
        if obs.get('complete') is True:
            for component in goal['metric']['components']:
                value=obs.get('components',{}).get(component)
                if type(value) not in (int,float) or not math.isfinite(value):raise WorkflowError('完整觀測不能缺分子／分母。','invalid_result')
                if profile.get('mode')=='live' and type(value) is not int:raise WorkflowError('原始計數／最小貨幣單位需整數；平均值不能代替完成觀看人次。','invalid_result')
                if value<0 and component not in ('net_after_all_costs_minor','net_attributed_receipts_minor'):raise WorkflowError('觀測計數或費用不能為負。','invalid_result')
            if type(obs.get('sample_size')) is not int or obs['sample_size']<0:
                raise WorkflowError('樣本數需可核對非負整數。','invalid_result')
            if profile.get('mode')=='live':
                from .execution import measurement_contract
                expected_unit=measurement_contract(goal)['sample_unit']
                if expected_unit=='account_snapshots' and obs['sample_size']!=1:raise WorkflowError('同一帳號本期只有一個比較快照，不能用粉絲數灌大樣本。','invalid_result')
                if expected_unit in goal['metric']['components'] and obs.get('sample_unit')==expected_unit and obs['sample_size']!=obs['components'][expected_unit]:
                    raise WorkflowError('樣本數需對上本目標原始分母；不能另外填大數字過樣本門檻。','invalid_result')
                qualified=[c for c in goal['metric']['components'] if c.startswith('qualified_')]
                if qualified:
                    rule=profile['goal'].get('qualification_rule')
                    if not rule or obs.get('qualification_rule_sha256')!=digest(rule):raise WorkflowError('合格判定需沿用事前登記規則。','invalid_result')
                    for component in qualified:
                        records=obs.get('component_records',{}).get(component)
                        if not isinstance(records,list) or len(records)!=obs['components'][component]:raise WorkflowError('合格數需逐筆原始紀錄，不接受模型直接填總數。','invalid_result')
                        identities=set()
                        for record in records:
                            stamp=record.get('occurred_at') if isinstance(record,dict) else None
                            if not isinstance(record,dict) or not record.get('id') or record.get('account_ref')!=obs['account_ref'] or not _proof(record.get('source_ref'),profile['mode']) or type(stamp) not in (int,float) or not math.isfinite(stamp) or not window['start']<=stamp<=window['end']:
                                raise WorkflowError('合格紀錄缺原始ID、正確帳號、來源或本期時間。','invalid_result')
                            identities.add(record['id'])
                        if len(identities)!=len(records):raise WorkflowError('同人／同一單位不能重複算合格成果。','invalid_result')
            if any(c in goal['metric']['components'] for c in ('external_purchases','repeat_buyers','paid_contracts')):
                from .accounting import Ledger
                Ledger(self.store)
                ids=obs.get('economic_ids',[])
                actual=[]
                if profile.get('mode')=='live' and obs.get('payments_complete') is not True:
                    raise WorkflowError('購買成效需確認本期金流資料完整度，單筆事件不足以證明整段期間。','invalid_result')
                def eligible(event):
                    if not (event.get('external_customer') is True and not event.get('test') and window['start']<=event.get('occurred_at',-1)<=window['end']):return False
                    if obs.get('currency') and event.get('currency')!=obs['currency']:return False
                    if profile.get('mode')=='live':
                        if type(event.get('amount_minor')) is not int or event['amount_minor']<=0:return False
                        scope=event.get('attribution',{})
                        if not isinstance(scope,dict) or scope.get('run_id')!=task['run_id'] or scope.get('channel')!=task['data']['channel'] or scope.get('account_ref')!=obs['account_ref']:return False
                    return True
                for economic_id in ids:
                    row=self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND economic_id=? AND kind='payment'",(profile['brand']['url'],economic_id)).fetchone()
                    if row:
                        event=json.loads(row[0])
                        if eligible(event):actual.append(economic_id)
                # Pending receipt rows cannot replace a canonical payment's
                # original attribution/customer classification.
                for event in data.get('money_events',[]):
                    if event.get('kind')!='payment' or event.get('verified') is not True or event.get('economic_id') not in ids:continue
                    saved=self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND economic_id=? AND kind='payment'",(profile['brand']['url'],event['economic_id'])).fetchone()
                    if eligible(json.loads(saved[0]) if saved else event):actual.append(event['economic_id'])
                if profile.get('mode')=='live' and ('退款後' in goal['title'] or 'refund' in goal['metric']['key']):
                    if obs.get('refunds_complete') is not True:
                        raise WorkflowError('退款後購買需已核對的退款完整度，不能把未知退款當0。','invalid_result')
                    kept=[]
                    for economic_id in set(actual):
                        payment=self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND economic_id=? AND kind='payment'",(profile['brand']['url'],economic_id)).fetchone()
                        payment=json.loads(payment[0]) if payment else next((e for e in data.get('money_events',[]) if e.get('economic_id')==economic_id),{})
                        refunds=[json.loads(e[0]) for e in self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND kind='refund'",(profile['brand']['url'],))]
                        refunds += [e for e in data.get('money_events',[]) if e.get('kind')=='refund' and e.get('verified') is True]
                        matched={e['economic_id']:e for e in refunds if e.get('payment_economic_id')==economic_id and e.get('currency')==payment.get('currency') and e.get('occurred_at',float('inf'))<=window['end']}
                        if payment.get('amount_minor') is None or any(e.get('amount_minor') is None for e in matched.values()):
                            raise WorkflowError('退款後購買尚缺原付款／退款金額。','invalid_result')
                        if payment['amount_minor']>sum(e['amount_minor'] for e in matched.values()):kept.append(economic_id)
                    actual=kept
                def payment_data(ident):
                    row=self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND economic_id=? AND kind='payment'",(profile['brand']['url'],ident)).fetchone()
                    return json.loads(row[0]) if row else next((e for e in data.get('money_events',[]) if e.get('economic_id')==ident),{})
                if 'external_purchases' in goal['metric']['components'] and len(set(actual))!=obs['components']['external_purchases']:
                    raise WorkflowError('外部購買數需對應本品牌已核對且去重的交易。','invalid_result')
                if 'repeat_buyers' in goal['metric']['components']:
                    customers={payment_data(i).get('customer_ref') for i in set(actual)}
                    if None in customers:raise WorkflowError('續購需穩定匿名客戶ID，不以付款筆數代替人數。','invalid_result')
                    previous=[json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND kind='payment' AND occurred<?",(profile['brand']['url'],window['start']))]
                    previous={e.get('customer_ref') for e in previous if e.get('external_customer') is True and not e.get('test') and type(e.get('amount_minor')) is int and e['amount_minor']>0 and (not obs.get('currency') or e.get('currency')==obs['currency'])}
                    if len(customers & previous)!=obs['components']['repeat_buyers']:
                        raise WorkflowError('續購人數需原客戶過去付款及本期付款的交集。','invalid_result')
                if 'paid_contracts' in goal['metric']['components']:
                    contracts=set()
                    for ident in set(actual):
                        payment=payment_data(ident)
                        evidence=payment.get('contract_evidence',[])
                        evidence=[evidence] if isinstance(evidence,str) else evidence
                        if not isinstance(payment.get('contract_ref'),str) or not payment['contract_ref'] or payment.get('contract_paid_in_full') is not True or not isinstance(evidence,list) or not evidence or any(not _proof(e,profile.get('mode','prepare')) for e in evidence):
                            raise WorkflowError('已收款採購需原合約／發票及完整收款證據，不能拿商機階段代替。','invalid_result')
                        contracts.add(payment['contract_ref'])
                    if len(contracts)!=obs['components']['paid_contracts']:
                        raise WorkflowError('採購需按已核對合約去重，不以分期款筆數灌大。','invalid_result')
            if 'net_attributed_receipts_minor' in goal['metric']['components']:
                from .accounting import Ledger
                if profile.get('mode')=='live' and (obs.get('payments_complete') is not True or obs.get('refunds_complete') is not True):
                    raise WorkflowError('退款後ROAS需本期付款及退款來源完整。','invalid_result')
                summary=Ledger(self.store).summary(profile['brand']['url'],obs.get('currency',''),start=window['start'],end=window['end'],attribution_scope={'run_id':task['run_id']} if profile.get('mode')=='live' else None)
                gross,refunds=summary['gross_receipts_minor'],summary['refunds_minor']
                if gross is None or refunds is None or obs['components']['net_attributed_receipts_minor']!=gross-refunds:
                    raise WorkflowError('ROAS收入需對上原付款及退款帳本。','invalid_result')
        if task['data']['measurement_role']=='baseline' and obs.get('complete') is not True:
            raise WorkflowError('基準缺資料請交waiting_external，不能先執行測試再補基準。','invalid_result')
        return obs

    def record(self,task,profile,data):
        if not task['data'].get('measurement_role'):return
        obs=self.check_observation(task,profile,data)
        branch=task['data']['branch'];role=task['data']['measurement_role']
        kind='goal.baseline.'+branch if role=='baseline' else 'goal.observation.'+digest(task['key'])
        scope_cycle=0 if role=='baseline' else task['cycle']
        old=self.store.artifact(task['run_id'],scope_cycle,kind)
        if old and digest(old)!=digest(obs):raise WorkflowError('既有基準或觀測不能原地改寫。','payload_changed')
        self.store.artifact(task['run_id'],scope_cycle,kind,obs)
        if role=='current':self.store.artifact(task['run_id'],task['cycle'],'goal.current.'+branch,obs)

    def after(self,task,result):
        if task['data'].get('continuity'):return
        if task['data'].get('role')!='goal_stage':return
        data=result.get('data',{})
        empty=result.get('execution_level')=='no_results' or ('demand_qualification' in data and not data['demand_qualification']['qualified'])
        if not empty:return
        branch=task['data']['branch']
        self.store.artifact(task['run_id'],task['cycle'],'goal.empty_branch.'+branch,{
            'source_task':task['key'],'counts':data.get('demand_qualification',{}).get('counts',{}),
            'reason':'No eligible results; no outreach was performed','outcome_improvement_claim':False})
        rows=self.store.db.execute("SELECT key,data FROM tasks WHERE run_id=? AND cycle=? AND rowid>(SELECT rowid FROM tasks WHERE key=?) ORDER BY rowid",(task['run_id'],task['cycle'],task['key'])).fetchall()
        for row in rows:
            node=json.loads(row['data'])
            if node.get('branch')!=branch or node.get('measurement_role'):continue
            node['branch_skip']={'reason':'no eligible results','source_task':task['key']}
            # A skip still waits for its dependencies (notably the pinned
            # baseline); finishing it now would open downstream work too soon.
            self.store.db.execute("UPDATE tasks SET data=? WHERE key=? AND state='ready'",(canonical(node),row['key']))
        self.store.event(task['run_id'],task['key'],'goal_branch_no_results',{'branch':branch,'other_branches_continue':True})

    def metric(self,goal,observations,minimum_sample):
        reasons=[];totals={key:0 for key in goal['metric']['components']};currencies=set();models=set()
        for obs in observations:
            if not obs or obs.get('complete') is not True or obs.get('conversion_delay_complete') is not True:reasons.append('incomplete_or_delayed');continue
            if type(obs.get('sample_size')) is not int or obs['sample_size']<minimum_sample:reasons.append('insufficient_sample')
            if obs.get('attribution')=='unknown' or not obs.get('attribution'):reasons.append('attribution_unknown')
            models.add(obs.get('attribution'));currencies.add(obs.get('currency'))
            if goal['guardrail_metric']=='all_costs_complete' and obs.get('all_costs_complete') is not True:reasons.append('unknown_operating_costs')
            for key in totals:
                value=obs.get('components',{}).get(key)
                if type(value) not in (int,float) or not math.isfinite(value):reasons.append('missing_component:'+key)
                else:totals[key]+=value
        if len(models)>1:reasons.append('mixed_attribution_models')
        if goal['metric']['formula'] in ('cost','ratio') and (len(currencies)!=1 or None in currencies):reasons.append('unknown_or_mixed_currency')
        values=list(totals.values())
        if goal['metric']['formula']=='count':value=values[0]
        else:value=values[0]/values[1] if values[1]>0 else None
        if value is None:reasons.append('zero_denominator')
        return {'value':value if not reasons else None,'components':totals,'reasons':sorted(set(reasons)),'attribution':sorted(models,key=str),'currency':sorted(currencies,key=str)}

    def evaluate(self,task,profile):
        goal=goal_definition(profile['goal']['id']);setting=profile['goal']
        graph=self.store.artifact(task['run_id'],task['cycle'],'graph.definition')
        branches=sorted(graph['branches'])
        baseline=[self.store.artifact(task['run_id'],0,'goal.baseline.'+b) for b in branches]
        current=[self.store.artifact(task['run_id'],task['cycle'],'goal.current.'+b) for b in branches]
        threshold=setting['target']['type']=='threshold'
        baseline_minimum=0 if threshold else setting['minimum_sample']
        a=self.metric(goal,baseline,baseline_minimum);b=self.metric(goal,current,setting['minimum_sample'])
        # A current-value threshold can start from a recorded empty cohort.
        # Its old 0/0 rate remains undefined, never fabricated as zero.
        baseline_reasons=[r for r in a['reasons'] if not (threshold and r=='zero_denominator')]
        reasons=baseline_reasons+b['reasons']
        if a['attribution']!=b['attribution'] or a['currency']!=b['currency']:reasons.append('baseline_current_source_mismatch')
        for branch,old,new in zip(branches,baseline,current):
            empty=self.store.artifact(task['run_id'],task['cycle'],'goal.empty_branch.'+branch)
            if old and new and not empty and (new['window']['start']<old['window']['end'] or new['window']['end']-new['window']['start']<setting.get('observation_seconds',goal['observation_seconds'])):reasons.append('overlapping_or_short_observation')
        for branch,new in zip(branches,current):
            anchor=self.store.artifact(task['run_id'],task['cycle'],'goal.intervention_anchor.'+branch)
            if new and anchor and new['window']['start']<anchor['at']-300:
                reasons.append('observation_predates_intervention')
        if goal['guardrail_metric']=='all_costs_complete':
            from .accounting import Ledger
            for obs in baseline+current:
                if not obs:continue
                scope={'run_id':task['run_id']} if profile.get('mode')=='live' else None
                money=Ledger(self.store).summary(profile['brand']['url'],obs.get('currency',''),start=obs['window']['start'],end=obs['window']['end'],reserve_complete=obs.get('reserves_complete') is True,attribution_scope=scope)
                net=money['net_after_operating_cost_minor'];reserve=money['future_delivery_reserve_minor']
                if net is None or reserve is None or obs.get('components',{}).get('net_after_all_costs_minor')!=net-reserve:reasons.append('net_not_reconciled_with_all_costs_and_reserves')
        sign=1 if goal['metric']['direction']=='increase' else -1
        change=None if a['value'] is None or b['value'] is None else sign*(b['value']-a['value'])
        target=setting['target'];score=change
        if target['type']=='relative_percent':
            if a['value'] in (None,0):reasons.append('relative_target_without_nonzero_baseline');score=None
            else:score=change/abs(a['value'])*100 if change is not None else None
        snapshot=goal['metric']['key'] in ('net_followers','net_subscribers')
        if target['type']=='threshold':
            # Snapshot totals are not new followers/subscribers. A threshold of
            # 100 means +100 in this experiment, even on an account with 10k.
            score=change if snapshot else sign*b['value'] if b['value'] is not None else None
        violated=[]
        for guard in setting.get('guardrails',[]):
            for obs in current:
                value=(obs or {}).get('guardrail_values',{}).get(guard['metric'])
                if type(value) not in (int,float) or not math.isfinite(value):reasons.append('missing_guardrail:'+guard['metric'])
                elif (guard['direction']=='maximum' and value>guard['threshold']) or (guard['direction']=='minimum' and value<guard['threshold']):violated.append(guard['metric'])
        reached=not reasons and not violated and score is not None and score>=(sign*target['value'] if target['type']=='threshold' else target['value'])
        short_empty=any(self.store.artifact(task['run_id'],task['cycle'],'goal.empty_branch.'+branch) and obs and obs['window']['end']-obs['window']['start']<setting.get('observation_seconds',goal['observation_seconds']) for branch,obs in zip(branches,current))
        if reached and short_empty:
            reached=False;reasons.append('empty_branch_window_incomplete')
        result={'goal_id':goal['id'],'baseline':a,'current':b,'directional_change':change,'score':score,'target':target,'reasons':sorted(set(reasons)),
                'decision':'stop_guardrail' if violated else 'target_reached' if reached else 'wait' if reasons else 'strategy_review','violated_guardrails':sorted(set(violated)),'fixture_only':profile.get('mode')=='fixture','causality_claim':False,'method_adoption_requires_separate_uncertainty_experiment':True,
                'target_value_kind':'net_change_from_pinned_snapshot' if snapshot and target['type']=='threshold' else target['type']}
        empty=[self.store.artifact(task['run_id'],task['cycle'],'goal.empty_branch.'+branch) for branch in branches]
        if result['decision'] in ('wait','strategy_review') and any(empty) and set(reasons).issubset({'zero_denominator','insufficient_sample'}):
            result.update(decision='strategy_review',no_results_search_revision=True,
                limitation='Only a bounded query/audience revision; no success or rate improvement is asserted.')
        from .continuity import state, phase_cycle, discover, finish
        pursuing=state(self.store,task['run_id'])
        prior={}
        for row in self.store.db.execute("SELECT cycle,data FROM artifacts WHERE run_id=? AND kind LIKE 'goal.evaluation.%' AND cycle<? ORDER BY created,rowid",(task['run_id'],task['cycle'])):prior[row['cycle']]=json.loads(row['data'])
        if pursuing:prior={k:v for k,v in prior.items() if k>=pursuing['phase_start_cycle']}
        previous=list(prior.values())[-1] if prior else None
        previous_value=(previous or {}).get('current',{}).get('value')
        result['round_change']=sign*(b['value']-previous_value) if previous_value is not None and b['value'] is not None else change
        window=list(prior.values())[-(setting.get('max_stagnant_cycles',3)-1):]+[result]
        if result['decision']=='strategy_review' and len(window)>=setting.get('max_stagnant_cycles',3) and all(r.get('round_change',r.get('directional_change')) is not None and r.get('round_change',r.get('directional_change'))<=0 for r in window):result['decision']='stop_no_progress'
        if result['decision']=='wait':self.observe_again(task,profile,branches,result)
        elif result['decision'].startswith('stop_'):pass
        elif not reached:
            if phase_cycle(self.store,task['run_id'],task['cycle'])>=profile['workflow'].get('max_iterations',2):result['decision']='stop_iteration_limit'
            else:
                ident='A-REVIEW' if goal['channel']=='audience' else 'S34'
                key=self.enqueue_skill(task['run_id'],task['cycle'],'review','strategy.'+str(task['data'].get('check',0)),ident,[task['key']])
                self.store.enqueue(task['run_id'],task['cycle'],'flow.goal.advance.'+str(task['data'].get('check',0)),{'role':'goal_advance','check':task['data'].get('check',0)},[key])
        self.store.artifact(task['run_id'],task['cycle'],'goal.evaluation.'+task['kind'],result)
        if result['decision'] in ('target_reached','stop_guardrail'):
            self.store.artifact(task['run_id'],0,'goal.latest_terminal',result)
            finish(self.store,task['run_id'],result['decision'])
        elif result['decision'] in ('stop_iteration_limit','stop_no_progress','stop_observation_limit'):
            discover(self.store,task,result['decision'])
        return result

    def observe_again(self,task,profile,branches,result,reset=False):
        from .continuity import state, discover
        p=state(self.store,task['run_id'])
        check=1 if reset else task['data'].get('check',0)+1
        if check>profile['workflow'].get('max_feedback_checks',14):
            result['decision']='stop_observation_limit';discover(self.store,task,result['decision']);return
        watch='watch.'+str((p or {}).get('observation_round',0))+'.' if (p or {}).get('observation_round') else ''
        goal=goal_definition(profile['goal']['id']);graph=self.store.artifact(task['run_id'],task['cycle'],'graph.definition');previous=[]
        for branch in branches:
            ident=next(n['skill'] for n in reversed(graph['nodes']) if n['branch']==branch and n.get('measurement_role')=='current')
            old=self.store.artifact(task['run_id'],0,'goal.baseline.'+branch)
            # A missing baseline cannot be manufactured after intervention.
            if not old or old.get('complete') is not True:
                result['decision']='waiting_baseline_data';return
            previous.append(self.enqueue_skill(task['run_id'],task['cycle'],branch,'observe.'+watch+str(check),ident,[task['key']],measurement_role='current',delay=profile['goal'].get('check_cadence_seconds',goal['check_cadence_seconds'])))
        self.store.enqueue(task['run_id'],task['cycle'],'flow.goal.evaluate.'+watch+str(check),{'role':'goal_evaluate','check':check},previous)

    def advance(self,task,profile):
        review=self.store.artifact(task['run_id'],task['cycle'],'flow_review_latest') or {}
        plan=review.get('next_iteration',{});goal=goal_definition(profile['goal']['id'])
        from .continuity import state, discover, new_phase, phase_cycle, transition, repair_preparation, save
        pursuing=state(self.store,task['run_id'])
        if pursuing:
            if self.store.db.execute("SELECT 1 FROM operations WHERE task_key IN (SELECT key FROM tasks WHERE run_id=?) AND state IN ('in_flight','uncertain')",(task['run_id'],)).fetchone():
                transition(self.store,task['run_id'],'awaiting_reconciliation','original_operation_unconfirmed');return {'decision':'waiting_for_dependency'}
            if task['data'].get('continuity'):
                repair=plan.get('preparation_repair')
                if repair:return {'decision':'preparation_repair_queued','task_key':repair_preparation(self.store,task['run_id'],repair.get('task_key'),repair.get('instruction'),repair.get('sources'))}
                original=[self.store.task(r[0]) for r in self.store.db.execute("SELECT key FROM tasks WHERE run_id=? AND state NOT IN ('done','cancelled') AND key<>?",(task['run_id'],task['key']))]
                if any(not t['data'].get('continuity') for t in original):
                    transition(self.store,task['run_id'],'waiting_dependency','original_required_work_preserved');return {'decision':'waiting_for_dependency'}
                latest=self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'goal.evaluation.%' ORDER BY created DESC,rowid DESC LIMIT 1",(task['run_id'],)).fetchone()
                if latest and json.loads(latest[0]).get('decision') in ('wait','stop_observation_limit'):
                    pursuing['observation_round']=pursuing.get('observation_round',0)+1;save(self.store,task['run_id'],pursuing)
                    graph=self.store.artifact(task['run_id'],task['cycle'],'graph.definition');result={'decision':'wait'}
                    self.observe_again(task,profile,sorted(graph['branches']),result,reset=True);return result
        if plan.get('decision')=='iterate':
            variable=plan.get('changed_variable');change=plan.get('change');hypothesis=plan.get('hypothesis')
            if variable not in ('audience','angle','channel','content_format','followup','query') or not isinstance(change,str) or not change or not hypothesis:
                raise WorkflowError('下一輪需事前假設及一項具體可執行改變；不能只說再試。','invalid_result')
            if variable=='channel':raise WorkflowError('換渠道需建立相應的新目標與權限範圍，不能在原graph只改文字。','new_goal_required')
            latest=[json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND cycle=? AND kind LIKE 'goal.evaluation.%' ORDER BY created,rowid",(task['run_id'],task['cycle']))]
            if latest and latest[-1].get('no_results_search_revision') and variable not in ('query','audience'):
                raise WorkflowError('沒有合格對象時，只能有界修正查詢／受眾，不盲目重發或宣稱賣點有效。','invalid_result')
            normalized=' '.join(change.split()).casefold()
            for row in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind='goal.change'",(task['run_id'],)):
                old=json.loads(row[0])
                if old.get('changed_variable')==variable and ' '.join(old.get('change','').split()).casefold()==normalized:
                    result={'decision':'stop_repeated_change','reason':'No new test: the same variable and change were already executed. Collect new buyer evidence before another run.'}
                    self.store.artifact(task['run_id'],task['cycle'],'goal.stop_reason',result)
                    discover(self.store,task,result['decision'])
                    return result
            if not pursuing and task['cycle']>=profile['workflow'].get('max_iterations',2):return {'decision':'stop_iteration_limit'}
            if pursuing and (phase_cycle(self.store,task['run_id'],task['cycle'])>=profile['workflow'].get('max_iterations',2) or pursuing.get('last_reason') in ('stop_no_progress','stop_iteration_limit')):new_phase(self.store,task['run_id'],task['cycle']+1)
            restart=plan.get('restart_at_channel_skill') or next(iter(goal['bottleneck_routes'].values()))
            self.seed(task['run_id'],task['cycle']+1,{'hypothesis':hypothesis,'changed_variable':variable,'change':change,'evidence_task':task['key']},restart,plan.get('delay_seconds',0))
            return {'decision':'next_test_created','cycle':task['cycle']+1,'hypothesis':hypothesis,'causality_claim':False}
        if plan.get('decision')=='wait':
            graph=self.store.artifact(task['run_id'],task['cycle'],'graph.definition')
            result={'decision':'wait'}
            self.observe_again(task,profile,sorted(graph['branches']),result);return result
        reason=plan.get('reason','No valid next test')
        if discover(self.store,task,reason):return {'decision':'new_evidence_scheduled','reason':reason}
        return {'decision':'stop','reason':reason}
