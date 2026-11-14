"""Brand-local, preregistered evidence comparisons and reversible method adoption."""
import json
import math
from .files import canonical, digest, redact
from .errors import WorkflowError

COSTS = {'ads', 'platform_payment', 'commission', 'partner_share', 'delivery', 'people', 'software', 'other'}
METHOD_FIELDS = {'skill_method', 'graph_method'}
STRATEGY_FIELDS = {'audience', 'angle', 'channel', 'followup', 'content_format', 'query'}


class Learning:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS strategy_memory(
          brand TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL,
          body TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS method_versions(
          brand TEXT NOT NULL, id TEXT NOT NULL, parent TEXT, body TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS experiments(
          brand TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
          result TEXT, created REAL NOT NULL, PRIMARY KEY(brand,id));
        CREATE TABLE IF NOT EXISTS method_heads(
          brand TEXT PRIMARY KEY, version TEXT NOT NULL, previous TEXT);
        ''')

    def brand(self, run_id):
        return self.store.config(run_id)['brand']['url']

    def remember(self, run_id, kind, body):
        if not body.get('sources') or not body.get('observed_at'):
            raise ValueError('記憶需要來源與觀察時間')
        brand, ident = self.brand(run_id), digest({'kind': kind, 'body': body})
        self.db.execute('INSERT OR IGNORE INTO strategy_memory VALUES(?,?,?,?,?)', (brand, ident, kind, canonical(redact(body)), self.store.clock()))
        return {'id': ident, 'brand': brand}

    def search(self, run_id, kind=None, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('分頁範圍錯誤')
        rows = self.db.execute('SELECT rowid,id,kind,body FROM strategy_memory WHERE brand=? AND rowid>? AND (? IS NULL OR kind=?) ORDER BY rowid LIMIT ?', (self.brand(run_id), after, kind, kind, limit + 1)).fetchall()
        return {'items': [{'cursor': r[0], 'id': r[1], 'kind': r[2], 'body': json.loads(r[3])} for r in rows[:limit]], 'next_cursor': rows[limit-1][0] if len(rows) > limit else None}

    def propose(self, run_id, body):
        # Only operational strategy is mutable; no account, budget or product fields.
        if not body.get('reason') or not body.get('sources') or not body.get('changes') or set(body['changes']) - (STRATEGY_FIELDS | METHOD_FIELDS):
            raise ValueError('候選版只能變更客群、切角、渠道、跟進及內容格式，且需要來源與理由')
        for field in METHOD_FIELDS.intersection(body['changes']):
            change = body['changes'][field]
            if field == 'skill_method':
                from .contracts import FIELDS
                from .channels import channel_catalog
                known=set(FIELDS)|{s['id'] for s in channel_catalog()['channel_skills']}
                if not isinstance(change, dict) or set(change) != {'skill_id', 'instructions'} or change['skill_id'] not in known or not isinstance(change['instructions'], str) or not change['instructions'].strip():
                    raise ValueError('Skill候選需要既有skill_id與附加方法，不能改交付／動作契約')
            else:
                from .graph import validate_graph
                validate_graph(change)
        brand = self.brand(run_id)
        head = self.db.execute('SELECT version FROM method_heads WHERE brand=?', (brand,)).fetchone()
        ident = digest(body)
        self.db.execute("INSERT OR IGNORE INTO method_versions VALUES(?,?,?,?,?,?)", (brand, ident, head[0] if head else None, canonical(redact(body)), 'candidate', self.store.clock()))
        return {'version': ident, 'state': 'candidate'}

    def preregister(self, run_id, body):
        body=json.loads(canonical(body))
        required = {'hypothesis', 'baseline', 'candidate', 'changed_variable', 'start_at', 'end_at', 'minimum_sample', 'metric', 'minimum_improvement', 'sources'}
        if not required.issubset(body) or not body['hypothesis'] or not body['sources'] or body['metric'] != 'net_per_customer':
            raise ValueError('需事前假設、基準／候選、單一變數與淨貢獻指標')
        if body['changed_variable'] not in (STRATEGY_FIELDS | METHOD_FIELDS) or type(body['minimum_sample']) is not int or body['minimum_sample'] < 1:
            raise ValueError('變數或樣本門檻無效')
        for key in ('start_at', 'end_at', 'minimum_improvement'):
            if type(body[key]) not in (int, float) or not math.isfinite(body[key]):
                raise ValueError('觀察窗口／門檻需有限數字')
        if body['start_at'] < self.store.clock() or body['end_at'] <= body['start_at'] or body['minimum_improvement'] < 0:
            raise ValueError('實驗需在觀察前登記，窗口與門檻不可倒置')
        brand = self.brand(run_id)
        for version in (body['baseline'], body['candidate']):
            if not self.db.execute('SELECT 1 FROM method_versions WHERE brand=? AND id=?', (brand, version)).fetchone():
                raise ValueError('比較版本不屬於本品牌')
        versions = [json.loads(self.db.execute('SELECT body FROM method_versions WHERE brand=? AND id=?', (brand, version)).fetchone()[0])['changes'] for version in (body['baseline'], body['candidate'])]
        changed = {key for key in set(versions[0]) | set(versions[1]) if versions[0].get(key) != versions[1].get(key)}
        if changed != {body['changed_variable']}:
            raise ValueError('基準與候選需只改登記的一個變數')
        context=body.get('comparison_context',{})
        if not isinstance(context,dict) or not all(context.get(k) for k in ('audience','channel','currency','outcome_definition')):
            raise ValueError('需事前固定受眾、渠道、幣別與結果定義')
        if body.get('assignment','observational') not in ('randomized','matched_cohort','time_block','observational'):raise ValueError('Invalid assignment design')
        from .evidence import evaluation_policy
        body['evaluation']=evaluation_policy(body)
        body['decision_policy']='fixed_horizon_uncertainty_and_full_costs_v1'
        loss=body.get('maximum_loss_per_customer',0)
        if type(loss) not in (int,float) or not math.isfinite(loss) or loss<0:raise ValueError('Invalid regression loss limit')
        if type(body.get('rollback_on_regression',False)) is not bool:raise ValueError('rollback_on_regression must be boolean')
        ident = digest(body)
        self.db.execute('INSERT OR IGNORE INTO experiments VALUES(?,?,?,?,?)', (brand, ident, canonical(redact(body)), None, self.store.clock()))
        return {'experiment_id': ident}

    def compare(self, run_id, ident, observations):
        brand = self.brand(run_id)
        row = self.db.execute('SELECT body,result FROM experiments WHERE brand=? AND id=?', (brand, ident)).fetchone()
        if not row:
            raise ValueError('無本品牌實驗')
        plan = json.loads(row[0]); reasons = []; values = {}
        evidence_digest=digest(observations)
        previous=json.loads(row[1]) if row[1] else None
        if previous and previous.get('decision') in ('adopt','retain','inconclusive'):
            if previous.get('evidence_digest')!=evidence_digest:raise ValueError('已結案實驗不能事後加樣本或改證據；請事前登記新實驗')
            return previous
        if self.store.clock() < plan['end_at']:
            reasons.append('observation_window_open')
        for arm in ('baseline', 'candidate'):
            sample = observations.get(arm, {})
            if sample.get('source_class') != 'verified_external' or not sample.get('sources'):
                reasons.append(arm + ':unverified_source')
            if sample.get('attribution') != 'comparable' or sample.get('complete') is not True or sample.get('conversion_delay_complete') is not True:
                reasons.append(arm + ':incomplete_or_unattributed')
            if sample.get('start_at') != plan['start_at'] or sample.get('end_at') != plan['end_at']:
                reasons.append(arm + ':period_mismatch')
            if sample.get('currency')!=plan.get('comparison_context',{}).get('currency'):
                reasons.append(arm + ':preregistered_currency_mismatch')
            count = sample.get('customers')
            if type(count) is not int or count < plan['minimum_sample']:
                reasons.append(arm + ':insufficient_customers')
            costs = sample.get('costs', {})
            numbers = [sample.get('receipts'), sample.get('refunds'), sample.get('reserves'), *[costs.get(c) for c in COSTS]]
            if any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in numbers):
                reasons.append(arm + ':unknown_money_or_cost')
            elif type(count) is int and count > 0:
                values[arm] = (numbers[0] - sum(numbers[1:])) / count
        result = {'decision': 'wait', 'reasons': reasons, 'values': values, 'observations': observations,
                  'experiment_id': ident, 'causality_claim': False, 'compared_at': self.store.clock(),'evidence_digest':evidence_digest,'uncertainty':None,'fixture_only':self.store.config(run_id).get('mode')=='fixture'}
        if observations.get('baseline', {}).get('currency') != observations.get('candidate', {}).get('currency') or not observations.get('baseline', {}).get('currency'):
            reasons.append('currency_mismatch_or_unknown')
        if 'evaluation' not in plan:
            reasons.append('legacy_experiment_requires_prospective_evidence_policy')
        if not reasons:
            from .evidence import uncertainty
            checked=uncertainty(plan,observations,values,self.store.config(run_id).get('mode','prepare'))
            reasons.extend(checked['reasons']);result['uncertainty']=checked['interval']
        if not reasons:
            old,new=observations['baseline'],observations['candidate']
            old_ratio=old['refunds']/old['receipts'] if old['receipts']>0 else 0
            new_ratio=new['refunds']/new['receipts'] if new['receipts']>0 else 0
            interval=result['uncertainty']
            if new_ratio>old_ratio+plan['evaluation']['refund_ratio_tolerance']:
                result['decision']='retain';reasons.append('refund_quality_regression')
            elif interval['lower']>plan['minimum_improvement']:result['decision']='adopt'
            elif interval['upper']<=plan['minimum_improvement']:result['decision']='retain'
            else:result['decision']='inconclusive';reasons.append('effect_uncertain_at_fixed_horizon')
        self.db.execute('UPDATE experiments SET result=? WHERE brand=? AND id=?', (canonical(result), brand, ident))
        self.remember(run_id, 'experiment_result', {'sources': [ident], 'observed_at': self.store.clock(), **result})
        return result

    def monitor(self,run_id,ident,observations):
        """Only a prospective, permitted regression plan may trigger rollback."""
        result=self.compare(run_id,ident,observations)
        row=self.db.execute('SELECT body FROM experiments WHERE brand=? AND id=?',(self.brand(run_id),ident)).fetchone()
        plan=json.loads(row[0]);head=self.db.execute('SELECT version FROM method_heads WHERE brand=?',(self.brand(run_id),)).fetchone()
        interval=result.get('uncertainty');threshold=plan.get('maximum_loss_per_customer',0)
        if type(threshold) not in (int,float) or not math.isfinite(threshold) or threshold<0:raise ValueError('Invalid preregistered regression loss limit')
        regressed=bool(interval and interval['upper'] < -threshold and result['decision']=='retain')
        if regressed and plan.get('rollback_on_regression') is True and head and head[0]==plan['candidate']:
            return {'decision':'rolled_back','comparison':result,'rollback':self.rollback(run_id,'Prospective experiment '+ident+' verified net regression')}
        return {'decision':'regression_alert' if regressed else 'retain_monitoring','comparison':result,'rollback_executed':False}

    def adopt(self, run_id, ident):
        brand = self.brand(run_id)
        row = self.db.execute('SELECT body,result FROM experiments WHERE brand=? AND id=?', (brand, ident)).fetchone()
        if not row or not row[1] or json.loads(row[1])['decision'] != 'adopt':
            raise ValueError('沒有可採用的完整證據')
        plan = json.loads(row[0]); version = plan['candidate']
        result=json.loads(row[1])
        if plan.get('decision_policy')!='fixed_horizon_uncertainty_and_full_costs_v1' or not result.get('uncertainty') or not result.get('evidence_digest'):
            raise ValueError('舊版點估計或缺少完整不確定性證據，不能直接採用；需新登記比較')
        head = self.db.execute('SELECT version FROM method_heads WHERE brand=?', (brand,)).fetchone()
        if head and head[0] != plan['baseline']:
            raise ValueError('目前基準已變更，需重新比較')
        self.db.execute('INSERT INTO method_heads VALUES(?,?,?) ON CONFLICT(brand) DO UPDATE SET previous=excluded.previous,version=excluded.version', (brand, version, plan['baseline']))
        self.db.execute("UPDATE method_versions SET state='adopted' WHERE brand=? AND id=?", (brand, version))
        self.store.event(run_id, None, 'method_adopted', {'version': version, 'experiment': ident})
        return self.dispatch(run_id, version)

    def dispatch(self, run_id, version):
        row = self.db.execute('SELECT body FROM method_versions WHERE brand=? AND id=?', (self.brand(run_id), version)).fetchone()
        body = json.loads(row[0]); tasks = []
        mapping = {'audience': 'S02', 'angle': 'S10', 'channel': 'S04', 'followup': 'S17', 'content_format': 'S10', 'query':'S08'}
        for field, value in body['changes'].items():
            if field in METHOD_FIELDS:
                self.store.artifact(run_id, 0, 'method.active.' + field, {'version': version, 'definition': value})
                continue
            key = self.store.enqueue(run_id, 0, 'flow.strategy.' + version + '.' + field,
                {'skill_id': mapping[field], 'stage_order': 0, 'stage_action': '執行有證據採用的策略調整。', 'role': 'strategy', 'method_version': version, 'changes': {field: value}})
            tasks.append(key)
        return {'version': version, 'tasks': tasks, 'permissions_unchanged': True}

    def rollback(self, run_id, reason):
        brand = self.brand(run_id)
        row = self.db.execute('SELECT version,previous FROM method_heads WHERE brand=?', (brand,)).fetchone()
        if not row or not row[1] or not reason:
            raise ValueError('無前版或未提供回退依據')
        with self.store.transaction():
            self.db.execute('UPDATE method_heads SET version=?,previous=NULL WHERE brand=?', (row[1], brand))
            self.db.execute("UPDATE method_versions SET state='rolled_back' WHERE brand=? AND id=?", (brand, row[0]))
            self.db.execute("UPDATE tasks SET state='cancelled' WHERE run_id=? AND kind LIKE ? AND state NOT IN ('done','running')", (run_id, 'flow.strategy.' + row[0] + '.%'))
            self.store.event(run_id, None, 'method_rollback', {'from': row[0], 'to': row[1], 'reason': reason})
        for field in METHOD_FIELDS:
            self.store.db.execute('DELETE FROM artifacts WHERE key=?', ('%s:0:method.active.%s' % (run_id, field),))
        return self.dispatch(run_id, row[1])
