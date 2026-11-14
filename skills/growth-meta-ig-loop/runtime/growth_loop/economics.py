"""Source-bound view of the existing Ledger; unknown completeness is not zero.

An agent can name a receipt installed by the operator. It cannot submit amounts,
verified flags, invoice contents or coverage. No payment or provider call occurs.
"""
import json
import math
import re
from .accounting import Ledger, COST_CATEGORIES
from .files import canonical, digest, redact
from .errors import WorkflowError
from urllib.parse import urlparse


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def proof(value, now):
    ref=value.get('source_ref') if isinstance(value,dict) else None
    valid=isinstance(ref,str) and 0<len(ref)<=2048 and not any(c.isspace() for c in ref)
    if valid and ref.startswith(('http:','https:')):
        parsed=urlparse(ref);valid=parsed.scheme=='https' and bool(parsed.hostname) and not any((parsed.username,parsed.password,parsed.query,parsed.fragment))
    return valid and bool(re.fullmatch('[a-f0-9]{64}',str(value.get('raw_sha256','')))) and number(value.get('observed_at')) and 0<=value['observed_at']<=now


class Economics:
    def __init__(self, store, worker):
        self.store, self.worker = store, worker
        self.config = worker.get('economics',{})
        self.ledger = Ledger(store)
        store.db.executescript('''
          CREATE TABLE IF NOT EXISTS financial_receipts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,body TEXT NOT NULL,binding TEXT NOT NULL,ledger_id TEXT);
          CREATE TABLE IF NOT EXISTS invoice_lines(id TEXT PRIMARY KEY,currency TEXT NOT NULL,total_minor INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS invoice_allocations(id TEXT PRIMARY KEY,line_id TEXT NOT NULL,run_id TEXT NOT NULL,amount_minor INTEGER NOT NULL,receipt_id TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS financial_claims(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,receipt_id TEXT NOT NULL);
        ''')

    def scope(self, run_id):
        p=self.store.config(run_id)
        return {'tenant_id':self.worker.get('commercial',{}).get('tenant_id'),'brand_id':p['brand']['url'],'product_id':p.get('product_id') or p['brand']['url'],'run_id':run_id}

    def source(self,run_id,source_id,account_ref,kind,settled=False,coverage_key=None):
        scope=self.scope(run_id);source=self.config.get('sources',{}).get(source_id)
        if not isinstance(source,dict) or source.get('enabled') is not True or not source.get('provider') or source.get('tenant_id')!=scope['tenant_id'] or run_id not in source.get('run_ids',[]) or source.get('source_class') not in ('provider_invoice','payment_processor','product_backend') or not account_ref or source.get('account_ref')!=account_ref:
            raise WorkflowError('Financial source is absent, revoked or belongs to another account.','scope_mismatch')
        if source.get('fixture_only') is True and self.store.config(run_id).get('mode')!='fixture':raise WorkflowError('Fixture invoices cannot verify a live run.','scope_mismatch')
        if kind not in source.get('allowed_kinds',[]):raise WorkflowError('Source cannot attest this financial kind.','scope_mismatch')
        if settled and source.get('verification') not in ('native_provider_signature','native_authenticated_readback'):
            raise WorkflowError('Owner or upstream MCP declarations cannot verify settlement.','scope_mismatch')
        if coverage_key is not None and coverage_key not in source.get('coverage_keys',[]):raise WorkflowError('Source cannot attest this coverage.','scope_mismatch')
        return source

    def original(self, run_id, ident):
        row=self.config.get('trusted_receipts',{}).get(ident)
        scope=self.scope(run_id)
        if not scope['tenant_id'] or not isinstance(row,dict) or any(row.get(k)!=v for k,v in scope.items()):
            raise WorkflowError('Host-installed original financial receipt for this run required.','scope_mismatch')
        source=self.source(run_id,row.get('source_id'),row.get('provider_account_ref'),row.get('kind'),row.get('status')=='settled')
        if not proof(row,self.store.clock()) or not row.get('method_version') or row.get('actor') not in ('human','buyer_ai','shared') or not row.get('economic_id'):
            raise ValueError('Original digest, method, actor and economic identity required')
        if row.get('status') not in ('settled','estimated','pending','unknown') or row.get('kind') not in ('payment','refund','cost','reserve','payout','fulfillment'):
            raise ValueError('Explicit financial status and kind required')
        if not re.fullmatch('[A-Z]{3}',str(row.get('currency',''))) or not number(row.get('occurred_at')) or not 0<=row['occurred_at']<=self.store.clock():
            raise ValueError('Original currency and occurrence time required')
        amount=row.get('amount_minor')
        if amount is not None and (type(amount) is not int or amount<0):raise ValueError('Money uses nonnegative integer minor units or null')
        if row['status']=='settled' and row['kind']!='fulfillment' and amount is None:raise ValueError('Settled amount cannot be unknown')
        if row['kind']=='cost' and row.get('category') not in COST_CATEGORIES:raise ValueError('Known ledger cost category required')
        identity={k:source.get(k) for k in ('provider','source_class','verification','tenant_id','account_ref','fixture_only')}
        return row, digest({'source_identity':identity,'receipt':row})

    def ingest(self, run_id, ident):
        row,binding=self.original(run_id,ident)
        old=self.store.db.execute('SELECT run_id,binding FROM financial_receipts WHERE id=?',(ident,)).fetchone()
        if old:
            if old['run_id']!=run_id or old['binding']!=binding:raise ValueError('Financial receipt identity or source changed')
            return {'receipt_id':ident,'duplicate':True,'new_charge':False}
        allocation=None;line=None;claim=None;economic_id=row['economic_id']
        if row['status']=='settled' and row['kind']=='cost':
            if not row.get('invoice_id') or not row.get('line_item_id') or type(row.get('original_line_amount_minor')) is not int or row['original_line_amount_minor']<row['amount_minor']:
                raise ValueError('Original invoice line and bounded allocation required')
            line=digest({'provider':self.config['sources'][row['source_id']]['provider'],**{k:row[k] for k in ('provider_account_ref','invoice_id','line_item_id')}})
            allocation=digest({'line':line,**{k:row[k] for k in ('run_id','method_version','actor','category')}})
            economic_id='invoice-allocation:'+allocation
        elif row['status']=='settled' and row['kind']!='fulfillment':
            claim=digest({'provider':self.config['sources'][row['source_id']]['provider'],**{k:row[k] for k in ('provider_account_ref','kind','economic_id')}})
        ledger_id=None
        with self.store.transaction():
            if line:
                original=self.store.db.execute('SELECT * FROM invoice_lines WHERE id=?',(line,)).fetchone()
                if original and (original['currency']!=row['currency'] or original['total_minor']!=row['original_line_amount_minor']):raise ValueError('Original invoice line changed')
                self.store.db.execute('INSERT OR IGNORE INTO invoice_lines VALUES(?,?,?)',(line,row['currency'],row['original_line_amount_minor']))
                previous=self.store.db.execute('SELECT amount_minor FROM invoice_allocations WHERE id=?',(allocation,)).fetchone()
                if previous and previous['amount_minor']!=row['amount_minor']:raise ValueError('Same invoice allocation changed; do not charge twice')
                used=self.store.db.execute('SELECT COALESCE(SUM(amount_minor),0) FROM invoice_allocations WHERE line_id=?',(line,)).fetchone()[0]
                if not previous and used+row['amount_minor']>row['original_line_amount_minor']:raise ValueError('Invoice line would be over-allocated across runs')
                self.store.db.execute('INSERT OR IGNORE INTO invoice_allocations VALUES(?,?,?,?,?)',(allocation,line,run_id,row['amount_minor'],ident))
            if claim:
                prior=self.store.db.execute('SELECT run_id FROM financial_claims WHERE id=?',(claim,)).fetchone()
                if prior and prior['run_id']!=run_id:raise ValueError('Original financial event already belongs to another run')
                self.store.db.execute('INSERT OR IGNORE INTO financial_claims VALUES(?,?,?)',(claim,run_id,ident))
            if row['status']=='settled' and row['kind']!='fulfillment':
                if row['kind']=='refund':
                    parent=self.store.db.execute("SELECT id FROM financial_receipts WHERE run_id=? AND json_extract(body,'$.kind')='payment' AND json_extract(body,'$.economic_id')=? AND json_extract(body,'$.provider_account_ref')=? AND json_extract(body,'$.currency')=?",(run_id,row.get('payment_economic_id'),row['provider_account_ref'],row['currency'])).fetchone()
                    if not parent:raise WorkflowError('Original same-account payment receipt required before refund.','waiting_capability')
                payload={k:row[k] for k in ('kind','currency','amount_minor','occurred_at')}
                payload.update(verified=True,economic_id=economic_id,source='operator_receipt:'+row['source_id'],evidence=[row['source_ref']],attribution={'run_id':run_id,'method_version':row['method_version'],'actor':row['actor']})
                for key in ('category','payment_economic_id','external_customer','test','bank_available','customer_ref'):
                    if key in row:payload[key]=row[key]
                if row['kind']=='payment' and (row.get('external_customer') not in (True,False) or row.get('test') not in (True,False)):raise ValueError('Original customer/test classification required')
                existing=self.store.db.execute('SELECT id,data FROM money_events WHERE brand=? AND economic_id=?',(row['brand_id'],economic_id)).fetchone()
                if existing:
                    original=json.loads(existing['data'])
                    immutable=('kind','amount_minor','currency','occurred_at','payment_economic_id','category','external_customer','test','bank_available','customer_ref','contract_ref','contract_paid_in_full')
                    if any(key in payload and original.get(key)!=payload[key] for key in immutable):raise ValueError('Original canonical financial event changed')
                    if original.get('attribution',{}).get('run_id')!=run_id:raise WorkflowError('Original ledger event belongs to another run.','scope_mismatch')
                    # Method/actor binding is additional receipt evidence. Do not
                    # rewrite the original signed event's channel/account lineage.
                    ledger_id=existing['id']
                else:ledger_id=self.ledger.add(row['brand_id'],payload)['event_id']
            self.store.db.execute('INSERT INTO financial_receipts VALUES(?,?,?,?,?)',(ident,run_id,canonical(row),binding,ledger_id))
        return {'receipt_id':ident,'status':row['status'],'ledger_event_id':ledger_id,'duplicate':False,'new_charge':False,'platform_request_created':False}

    def summary(self, run_id):
        scope=self.scope(run_id);now=self.store.clock();records=[];settled=[];ledger_seen=set();trusted_ids=set();untrusted_rows=[];pending_rows=[];trusted_rows={}
        for saved in self.store.db.execute('SELECT * FROM financial_receipts WHERE run_id=? ORDER BY rowid',(run_id,)):
            row=json.loads(saved['body']);trusted=False
            try:_,binding=self.original(run_id,saved['id']);trusted=binding==saved['binding']
            except (ValueError,WorkflowError):pass
            records.append({'receipt_id':saved['id'],'kind':row['kind'],'status':row['status'] if trusted else 'source_rebind_required','amount_minor':row.get('amount_minor') if trusted else None,'currency':row['currency'],'category':row.get('category'),'cost_component':row.get('cost_component'),'method_version':row['method_version'],'actor':row['actor'],'source_ref':row['source_ref'],'raw_sha256':row['raw_sha256'],'ledger_event_id':saved['ledger_id']})
            if trusted:trusted_ids.add(saved['id']);trusted_rows[saved['id']]=row
            else:untrusted_rows.append(row)
            if trusted and row['status']!='settled':pending_rows.append(row)
            if trusted and row['status']=='settled' and saved['ledger_id'] and saved['ledger_id'] not in ledger_seen:
                ledger_seen.add(saved['ledger_id']);settled.append(row)
        eligible_payment_ids={r['economic_id'] for r in settled if r['kind']=='payment' and r.get('external_customer') is True and r.get('test') is False}
        coverage=self.config.get('coverage',{}).get(run_id,{})
        currency=self.store.config(run_id).get('budget',{}).get('currency')
        start,end=coverage.get('window_start'),coverage.get('window_end')
        period_complete=number(start) and number(end) and 0<=start<end<=now
        if period_complete:settled=[r for r in settled if start<=r['occurred_at']<end]
        def complete(key):
            row=coverage.get(key,{})
            kind,_,cat=key.partition(':')
            try:source=self.source(run_id,row.get('source_id'),row.get('provider_account_ref'),kind,True,key)
            except (ValueError,WorkflowError,AttributeError,TypeError):return False
            def relevant(r):return r['kind']==kind and (not cat or r.get('category')==cat)
            unresolved=[r for r in pending_rows if relevant(r) and not any(s['kind']==r['kind'] and s['economic_id']==r['economic_id'] for s in settled)]
            expected={ident for ident,r in trusted_rows.items() if relevant(r) and r['status']=='settled' and period_complete and start<=r['occurred_at']<end}
            return bool(period_complete and scope['tenant_id'] and isinstance(row,dict) and all(row.get(k)==v for k,v in scope.items()) and row.get('complete') is True and proof(row,now) and row['observed_at']>=end and row.get('window_start')==start and row.get('window_end')==end
                and isinstance(source,dict)
                and row.get('currency')==currency and not any(relevant(r) and r['currency']!=currency for r in trusted_rows.values())
                and isinstance(row.get('receipt_ids'),list) and set(row['receipt_ids'])==expected and not any(relevant(r) for r in untrusted_rows) and not unresolved)
        def total(kind,category=None):
            rows=[r for r in settled if r['kind']==kind and r['currency']==currency and (category is None or r.get('category')==category)]
            if kind=='payment':rows=[r for r in rows if r.get('external_customer') is True and r.get('test') is False]
            if kind=='refund':rows=[r for r in rows if r.get('payment_economic_id') in eligible_payment_ids]
            return sum(r['amount_minor'] for r in rows) if rows else None
        categories={}
        for cat in COST_CATEGORIES:
            observed=total('cost',cat);covered=complete('cost:'+cat)
            categories[cat]={'amount_minor':(observed if observed is not None else 0) if covered else None,'recorded_settled_minor':observed,'coverage_complete':covered,'status':'verified_complete' if covered else 'unknown_or_partial'}
        full=sum(v['amount_minor'] for v in categories.values()) if all(v['coverage_complete'] for v in categories.values()) else None
        pending_latest={}
        for r in pending_rows:
            if r['kind']=='cost' and r['currency']==currency and not any(s['kind']=='cost' and s['economic_id']==r['economic_id'] for s in settled):
                key=(r['source_id'],r['economic_id'],r['method_version'],r['actor'])
                if key not in pending_latest or r['observed_at']>pending_latest[key]['observed_at']:pending_latest[key]=r
        estimates={cat:None for cat in COST_CATEGORIES}
        for cat in COST_CATEGORIES:
            rows=[r for r in pending_latest.values() if r.get('category')==cat]
            if rows and all(r['status']=='estimated' and r.get('amount_minor') is not None for r in rows):estimates[cat]=sum(r['amount_minor'] for r in rows)
        money={kind:{'amount_minor':(total(kind) if total(kind) is not None else 0) if complete(kind) else None,'recorded_settled_minor':total(kind),'coverage_complete':complete(kind)} for kind in ('payment','refund','reserve','payout')}
        gross,refund,reserve=[money[k]['amount_minor'] for k in ('payment','refund','reserve')]
        fulfilled=complete('fulfillment')
        net=gross-refund-full-reserve if all(v is not None for v in (gross,refund,full,reserve)) and fulfilled else None
        return redact({'run_id':run_id,'currency':currency,'period':{'start':start,'end':end,'closed':bool(period_complete)},'costs':categories,'estimated_costs_by_category_minor':estimates,'full_cost_minor':full,'money':money,'fulfillment_coverage_complete':fulfilled,
            'fixture_only':self.store.config(run_id).get('mode')=='fixture',
            'owner_and_test_payments_count_as_customer_revenue':False,
            'net_after_costs_refunds_and_delivery_reserve_minor':net,'receipts':records,
            'all_costs_verified':full is not None,'unknown_fields_are_not_zero':True,'receipt_reuse_creates_charge':False,
            'unbound_legacy_money_events':self.store.db.execute("SELECT COUNT(*) FROM money_events WHERE brand=? AND json_extract(data,'$.attribution.run_id')=? AND id NOT IN (SELECT ledger_id FROM financial_receipts WHERE run_id=? AND ledger_id IS NOT NULL)",(scope['brand_id'],run_id,run_id)).fetchone()[0],
            'trust':'Original operator-bound registry and source binding are required again after relocation; copied rows do not grant trust.'})
