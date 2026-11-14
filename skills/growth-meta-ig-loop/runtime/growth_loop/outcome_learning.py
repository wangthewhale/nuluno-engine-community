"""Prospective nonfinancial goal comparisons from original runtime observations.

Economic adoption continues to use Learning's full-cost, refund-aware policy.
An account snapshot is one unit, regardless of its follower count.
"""
import json
import math
from .files import canonical, digest
from .learning import Learning


class OutcomeLearning:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.learning = Learning(store)
        self.db.execute('''CREATE TABLE IF NOT EXISTS goal_experiments(
          brand TEXT NOT NULL,id TEXT NOT NULL,body TEXT NOT NULL,result TEXT,created REAL NOT NULL,
          PRIMARY KEY(brand,id))''')

    def register(self, run_id, body):
        from .goals import goal_definition
        from .execution import ECONOMIC_COMPONENTS
        required = {'goal_id', 'baseline', 'candidate', 'baseline_runs', 'candidate_runs', 'changed_variable',
                    'start_at', 'end_at', 'minimum_sample', 'minimum_improvement', 'metric_bounds', 'hypothesis', 'comparison_context', 'sources'}
        if not isinstance(body, dict) or set(body) != required or not body['hypothesis'] or not body['sources']:
            raise ValueError('需完整的事前非金錢目標實驗，含原任務分組、上下界及來源')
        body = json.loads(canonical(body)); goal = goal_definition(body['goal_id']); brand = self.learning.brand(run_id)
        if set(goal['metric']['components']) & ECONOMIC_COMPONENTS:
            raise ValueError('成交／金錢目標須用完整成本與退款的economic實驗，不能走非金錢捷徑')
        for key in ('start_at', 'end_at', 'minimum_improvement'):
            if type(body[key]) not in (int,float) or not math.isfinite(body[key]): raise ValueError('需有限數字')
        bounds = body['metric_bounds']
        if not isinstance(bounds,list) or len(bounds)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in bounds) or bounds[1]<=bounds[0]:
            raise ValueError('需事前固定本次每個獨立任務改善值的有限上下界')
        if body['start_at']<self.store.clock() or body['end_at']<=body['start_at'] or body['minimum_improvement']<0 or type(body['minimum_sample']) is not int or body['minimum_sample']<3:
            raise ValueError('需事前窗口與每組至少三個獨立任務；樣本不足不採用')
        versions=[]
        for key in ('baseline','candidate'):
            row=self.db.execute('SELECT body FROM method_versions WHERE brand=? AND id=?',(brand,body[key])).fetchone()
            if not row: raise ValueError('比較方法不屬於本品牌')
            versions.append(json.loads(row[0]))
        a,b=[v['changes'] for v in versions]
        if {key for key in set(a)|set(b) if a.get(key)!=b.get(key)}!={body['changed_variable']}:
            raise ValueError('只能改事前登記的一個方法變數')
        context=body['comparison_context']
        if not isinstance(context,dict) or not all(context.get(k) for k in ('audience','channel','outcome_definition')):
            raise ValueError('需固定買方情境、渠道與成果定義')
        runs=body['baseline_runs']+body['candidate_runs']
        if any(not isinstance(body[k],list) or len(body[k])<body['minimum_sample'] for k in ('baseline_runs','candidate_runs')) or len(set(runs))!=len(runs):
            raise ValueError('組別需不同的原始任務，不能重複計數或跨組')
        settings=set(); modes=set()
        for run in runs:
            p=self.store.config(run)
            if p['brand']['url']!=brand or p.get('goal',{}).get('id')!=goal['id']: raise ValueError('組別需同品牌與相同具體目標')
            settings.add(digest(p['goal']));modes.add(p.get('mode'))
            if self.db.execute("SELECT 1 FROM artifacts WHERE run_id=? AND (kind LIKE 'flow_request.%' OR kind='goal.method_trial')",(run,)).fetchone():
                raise ValueError('方法分組必須在任務請求或前一實驗產生前完成')
            if self.db.execute('SELECT 1 FROM operations WHERE task_key LIKE ?', (run+':%',)).fetchone():
                raise ValueError('已有外部操作，不能回頭補登記方法分組')
        if len(settings)!=1 or len(modes)!=1: raise ValueError('目標窗口、分母、門檻、品質限制與驗收模式須相同')
        body.update(family='goal',metric=goal['metric']['key'],unit='independent_registered_run',fixture_only='fixture' in modes,
                    definition_sha256=digest(goal),setting_sha256=next(iter(settings)),causality_claim=False)
        ident=digest(body)
        with self.store.transaction():
            self.db.execute('INSERT INTO goal_experiments VALUES(?,?,?,?,?)',(brand,ident,canonical(body),None,self.store.clock()))
            for arm in ('baseline','candidate'):
                for run in body[arm+'_runs']:
                    self.store.artifact(run,0,'goal.method_trial',{'experiment_id':ident,'arm':arm,'version':body[arm],
                        'method':versions[0 if arm=='baseline' else 1],'registered_at':self.store.clock(),
                        'start_at':body['start_at'],'end_at':body['end_at']})
        return {'experiment_id':ident,**body}

    def compare(self,run_id,ident):
        from .goals import goal_definition
        brand=self.learning.brand(run_id)
        row=self.db.execute('SELECT body,result FROM goal_experiments WHERE brand=? AND id=?',(brand,ident)).fetchone()
        if not row: raise ValueError('沒有本品牌的原始目標實驗')
        if row['result']: return json.loads(row['result'])
        plan=json.loads(row['body']);reasons=[];values={};evidence=[];windows=[]
        if self.store.clock()<plan['end_at']:reasons.append('observation_window_open')
        for arm in ('baseline','candidate'):
            samples=[]
            for run in plan[arm+'_runs']:
                profile=self.store.config(run)
                if digest(profile['goal'])!=plan['setting_sha256'] or digest(goal_definition(plan['goal_id']))!=plan['definition_sha256']:
                    reasons.append(run+':definition_changed');continue
                assignment=self.store.artifact(run,0,'goal.method_trial') or {}
                if assignment.get('experiment_id')!=ident or assignment.get('version')!=plan[arm]: reasons.append(run+':assignment_changed');continue
                rows=self.db.execute("SELECT key,data FROM artifacts WHERE run_id=? AND cycle=1 AND kind LIKE 'goal.evaluation.%' ORDER BY created,rowid",(run,)).fetchall()
                if not rows: reasons.append(run+':missing_original_goal_result');continue
                evaluation=json.loads(rows[-1]['data'])
                if evaluation.get('fixture_only')!=plan['fixture_only'] or evaluation['reasons'] or evaluation.get('violated_guardrails') or evaluation.get('no_results_search_revision'):
                    reasons.append(run+':incomplete_or_quality_regression');continue
                graph=self.store.artifact(run,1,'graph.definition')
                if not graph: reasons.append(run+':missing_original_graph');continue
                for branch in graph['branches']:
                    observation=self.store.artifact(run,1,'goal.current.'+branch)
                    if not observation or not plan['start_at']<=observation['window']['start']<observation['window']['end']<=plan['end_at']:
                        reasons.append(run+':outside_preregistered_window');continue
                    source=observation.get('source_class')
                    if source!=('controlled_fixture' if plan['fixture_only'] else 'live_tool'):
                        reasons.append(run+':unverified_goal_source');continue
                    account=observation.get('account_ref');window=observation['window']
                    windows.append((run,branch,account,window['start'],window['end']))
                    evidence.append({'run_id':run,'branch':branch,'observation_sha256':digest(observation),'evaluation_key':rows[-1]['key'],'evaluation_sha256':digest(evaluation)})
                score=evaluation.get('directional_change')
                if type(score) not in (int,float) or not math.isfinite(score) or not plan['metric_bounds'][0]<=score<=plan['metric_bounds'][1]:
                    reasons.append(run+':unknown_or_out_of_bound_change');continue
                samples.append(score)
            values[arm]={'independent_runs':len(samples),'samples':samples,'mean':sum(samples)/len(samples) if samples else None}
            if len(samples)<plan['minimum_sample']:reasons.append(arm+':insufficient_independent_runs')
        for i,a in enumerate(windows):
            for b in windows[i+1:]:
                if a[0]!=b[0] and a[1:3]==b[1:3] and max(a[3],b[3])<min(a[4],b[4]):reasons.append('overlapping_same_account_observations')
        result={'experiment_id':ident,'family':'goal','goal_id':plan['goal_id'],'metric':plan['metric'],'decision':'wait',
                'reasons':sorted(set(reasons)),'values':values,'original_evidence':evidence,'uncertainty':None,
                'fixture_only':plan['fixture_only'],'causality_claim':False,'sales_success_inferred':False}
        if not reasons:
            span=plan['metric_bounds'][1]-plan['metric_bounds'][0]
            half=sum(span*math.sqrt(math.log(4/.05)/(2*values[arm]['independent_runs'])) for arm in ('baseline','candidate'))
            delta=values['candidate']['mean']-values['baseline']['mean']
            result['uncertainty']={'lower':delta-half,'upper':delta+half,'method':'preregistered_bounded_hoeffding_95'}
            if delta-half>plan['minimum_improvement']:result['decision']='adopt'
            elif delta+half<=plan['minimum_improvement']:result['decision']='retain'
            else:result['decision']='inconclusive';result['reasons'].append('effect_uncertain_at_fixed_horizon')
            result['evidence_digest']=digest(evidence)
            self.db.execute('UPDATE goal_experiments SET result=? WHERE brand=? AND id=?',(canonical(result),brand,ident))
        return result

    def adopt(self,run_id,ident):
        result=self.compare(run_id,ident)
        if result['decision']!='adopt': raise ValueError('原始目標證據不足以採用候選方法')
        brand=self.learning.brand(run_id)
        plan=json.loads(self.db.execute('SELECT body FROM goal_experiments WHERE brand=? AND id=?',(brand,ident)).fetchone()[0])
        with self.store.transaction():
            head=self.db.execute('SELECT version FROM method_heads WHERE brand=?',(brand,)).fetchone()
            if head and head[0]!=plan['baseline']:raise ValueError('目前基準已變，需新比較')
            self.db.execute('INSERT INTO method_heads VALUES(?,?,?) ON CONFLICT(brand) DO UPDATE SET version=excluded.version,previous=excluded.previous',(brand,plan['candidate'],plan['baseline']))
            self.db.execute("UPDATE method_versions SET state='adopted' WHERE brand=? AND id=?",(brand,plan['candidate']))
            self.store.event(run_id,None,'goal_method_adopted',{'experiment_id':ident,'metric':plan['metric'],'business_revenue_claim':False})
        return self.learning.dispatch(run_id,plan['candidate'])
