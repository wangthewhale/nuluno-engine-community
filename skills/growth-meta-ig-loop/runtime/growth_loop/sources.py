"""Verified source ingress. Receiving an endpoint is not source provisioning."""
import hashlib
import hmac
import json
import math
import os

from .accounting import Ledger
from .errors import WorkflowError, Waiting
from .files import canonical, digest


class SourceIngress:
    def __init__(self, store, sources):
        self.store, self.sources = store, sources
        self.ledger = Ledger(store)

    def configured(self,run_id,source_id):
        config=self.sources.get(source_id)
        profile=self.store.config(run_id)
        if not isinstance(config,dict) or config.get('enabled') is not True:
            raise Waiting('成效來源尚未實際配置。','waiting_capability')
        if run_id not in config.get('run_ids',[]) or config.get('brand_url')!=profile['brand']['url']:
            raise WorkflowError('來源不屬於本品牌與工作。','scope_mismatch')
        if not config.get('account_ref') or config['account_ref']!=profile.get('bindings',{}).get(config.get('channel'),{}).get('account_ref'):
            raise WorkflowError('來源缺本品牌渠道帳號綁定。','scope_mismatch')
        return config,profile

    @staticmethod
    def identity(config):
        return digest({k:config.get(k) for k in ('kind','brand_url','channel','account_ref','provider_account_ref')})

    @staticmethod
    def minimal_event(event,config):
        obj=event.get('data',{}).get('object',{})
        clean={k:obj[k] for k in ('id','object','status','amount_received','amount','currency','payment_intent','customer') if k in obj}
        for key in ('customer','payment_intent'):
            if isinstance(clean.get(key),dict):clean[key]=clean[key].get('id')
        clean['metadata']={k:obj.get('metadata',{}).get(k) for k in config.get('expected_metadata',{})}
        return {**{k:event[k] for k in ('id','type','created','livemode','account') if k in event},'data':{'object':clean}}

    def retry_pending(self,run_id,source_id=None):
        """Retry verified saved monetary events; never fetch or send a payment."""
        rows=self.store.db.execute("SELECT kind,data FROM artifacts WHERE run_id=? AND cycle=0 AND kind LIKE 'source.event.%' ORDER BY created,rowid",(run_id,)).fetchall()
        results=[]
        for row in rows:
            saved=json.loads(row['data']);event=saved.get('verified_event');sid=saved.get('source_id')
            if not event or not sid or source_id and source_id!=sid:continue
            config,profile=self.configured(run_id,sid)
            if config.get('kind')!='stripe_webhook' or saved.get('source_identity')!=self.identity(config):
                raise WorkflowError('原驗章來源的商戶／品牌綁定已改變，不能跨來源續接。','scope_mismatch')
            with self.store.transaction():
                result=self._stripe(run_id,sid,config,event,profile)
                updated={**saved,'state':result['state'],'result':result,'retried_at':self.store.clock()}
                if result['state'] in ('applied','ignored'):updated.pop('verified_event',None)
                self.store.artifact(run_id,0,row['kind'],updated)
            results.append({'event_id':event['id'],**result})
            if len(results)>=100:break
        return {'processed':len(results),'events':results,'new_platform_requests':False}

    def ingest(self, run_id, source_id, raw, headers):
        config,profile=self.configured(run_id,source_id)
        if not isinstance(raw, bytes) or len(raw) > 512 * 1024:
            raise ValueError('Source payload must be bounded original bytes')
        secret = os.environ.get(config.get('secret_env', ''))
        if not secret:
            raise Waiting('此資料來源缺實際驗章密鑰。', 'waiting_capability')
        headers = {str(k).lower(): str(v) for k, v in headers.items()}
        kind = config.get('kind')
        if kind == 'stripe_webhook':
            parts = {}
            for part in headers.get('stripe-signature', '').split(','):
                key, sep, value = part.partition('=')
                if sep: parts.setdefault(key, []).append(value)
            try: stamp = int(parts.get('t', [''])[0])
            except ValueError: raise WorkflowError('付款來源缺有效簽章時間。', 'invalid_signature')
            expected = hmac.new(secret.encode(), str(stamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
            if abs(self.store.clock() - stamp) > 300 or not any(hmac.compare_digest(expected, s) for s in parts.get('v1', [])):
                raise WorkflowError('付款原始事件驗章失敗。', 'invalid_signature')
        elif kind == 'signed_observation':
            try: stamp = int(headers.get('x-growth-timestamp', ''))
            except ValueError: raise WorkflowError('觀測來源缺有效時間。', 'invalid_signature')
            expected = hmac.new(secret.encode(), str(stamp).encode() + b'.' + raw, hashlib.sha256).hexdigest()
            if abs(self.store.clock() - stamp) > 300 or not hmac.compare_digest('sha256=' + expected, headers.get('x-growth-signature', '')):
                raise WorkflowError('觀測來源驗章失敗。', 'invalid_signature')
        else:
            raise ValueError('Unsupported source protocol; install a verified provider adapter')
        event = json.loads(raw)
        json.dumps(event, allow_nan=False)
        if not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id']:
            raise ValueError('Source event requires a stable ID')
        ident = 'source.event.' + digest({'source': source_id, 'id': event['id']})
        old = self.store.artifact(run_id, 0, ident)
        if old and old['body_sha256'] != digest(raw):
            raise WorkflowError('相同來源事件ID內容衝突。', 'payload_changed')
        if old and old['state'] == 'applied':
            return {**old['result'], 'duplicate': True}
        with self.store.transaction():
            result = self._stripe(run_id, source_id, config, event, profile) if kind == 'stripe_webhook' else self._observation(run_id, source_id, config, event, profile)
            saved={'body_sha256': digest(raw), 'state': result['state'], 'result': result, 'verified_at': self.store.clock(),
                'source_id':source_id,'source_identity':self.identity(config)}
            if kind=='stripe_webhook' and result['state'].startswith('waiting_'):
                saved['verified_event']=self.minimal_event(event,config)
            self.store.artifact(run_id, 0, ident, saved)
            self.store.event(run_id, None, 'verified_source_ingested', {'source_id': source_id, 'event_id': event['id'], 'state': result['state']})
        pending=self.retry_pending(run_id,source_id) if kind=='stripe_webhook' and result.get('kind')=='payment' and result['state']=='applied' else None
        return {**result, 'duplicate': False,**({'pending_reconciliation':pending} if pending else {})}

    def _observation(self, run_id, source_id, config, event, profile):
        from .goals import goal_definition
        goal = goal_definition(profile['goal']['id'])
        observation = event.get('observation', {})
        cycle=event.get('cycle',1)
        if type(cycle) is not int or cycle<1 or not isinstance(observation,dict):
            raise ValueError('Observation cycle and body are invalid')
        if event.get('run_id') != run_id or event.get('goal_id') != goal['id'] or goal['id'] not in config.get('goal_ids', []):
            raise WorkflowError('觀測沒有綁定原工作與目標。', 'scope_mismatch')
        if observation.get('account_ref') != config['account_ref'] or observation.get('metric') != goal['metric']['key']:
            raise WorkflowError('觀測帳號／指標不符。', 'scope_mismatch')
        if set(observation.get('components', {})) - set(config.get('components', [])):
            raise WorkflowError('來源不能宣告未配置的成效欄位。', 'scope_mismatch')
        # An analytics adapter cannot turn an arbitrary count into paid orders.
        from .execution import ECONOMIC_COMPONENTS
        economic = ECONOMIC_COMPONENTS
        if set(observation.get('components', {})) & economic:
            raise WorkflowError('付款成效需從核實交易帳本核對，不能由分析事件直接宣告。', 'scope_mismatch')
        source_class = 'controlled_fixture' if profile.get('mode') == 'fixture' else 'live_tool'
        if observation.get('source_class') != source_class:
            raise WorkflowError('受控來源不能當真實觀測。', 'scope_mismatch')
        role=observation.get('role')
        if role not in config.get('allowed_roles',['current']):
            raise WorkflowError('此來源未授權寫入這種基準／當期觀測。','scope_mismatch')
        clean = {**observation,'run_id':run_id,'cycle':cycle, 'sources': [{'reference': 'event:' + event['id'], 'observed_at': self.store.clock()}]}
        ident = 'source.observation.' + digest({'source': source_id, 'event': event['id']})
        self.store.artifact(run_id, cycle, ident, {'source_id': source_id, 'goal_id': goal['id'], 'channel': config['channel'], 'observation': clean})
        return {'state': 'applied', 'kind': 'observation', 'artifact': ident, 'permission_changes': False}

    def _stripe(self, run_id, source_id, config, event, profile):
        if profile.get('mode') != 'fixture' and event.get('livemode') is not True:
            raise WorkflowError('測試付款不能匯入真實成效。', 'scope_mismatch')
        account = config.get('provider_account_ref')
        if not account or event.get('account', account) != account:
            raise WorkflowError('金流事件不是設定的商戶帳號。', 'scope_mismatch')
        obj = event.get('data', {}).get('object', {})
        typ = event.get('type')
        if typ == 'payment_intent.succeeded' and obj.get('object') == 'payment_intent' and obj.get('status') == 'succeeded':
            metadata = obj.get('metadata', {})
            expected = config.get('expected_metadata', {})
            if not expected or any(metadata.get(k) != v for k, v in expected.items()):
                return {'state': 'waiting_attribution', 'reason': 'Original provider metadata does not bind this payment to the configured campaign/run.'}
            customer = obj.get('customer')
            if isinstance(customer, dict): customer = customer.get('id')
            owners = set(config.get('owner_customer_ids', []))
            external = set(config.get('external_customer_ids', []))
            if owners & external:
                raise ValueError('Owner and external customer classifications conflict')
            if not customer or customer not in owners | external:
                return {'state': 'waiting_customer_classification', 'reason': 'Payment is real; owner/test versus external buyer classification is not established.'}
            row = {'kind': 'payment', 'economic_id': 'stripe:' + account + ':' + str(obj['id']),
                'amount_minor': obj.get('amount_received'), 'external_customer': customer in external,
                'test': event.get('livemode') is not True,
                'customer_ref': digest({'brand':profile['brand']['url'],'provider':account,'customer':customer})}
            if type(row['amount_minor']) is not int or row['amount_minor']<=0:
                return {'state':'waiting_paid_amount','reason':'A zero or unknown received amount is not an external paid purchase.'}
        elif typ in ('refund.created', 'refund.updated') and obj.get('object') == 'refund':
            if obj.get('status') != 'succeeded':
                return {'state': 'waiting_refund_settlement', 'reason': 'Refund has not succeeded.'}
            parent = obj.get('payment_intent')
            if isinstance(parent, dict): parent = parent.get('id')
            if not parent:
                return {'state': 'waiting_parent_payment', 'reason': 'Refund requires its original PaymentIntent readback.'}
            parent_id = 'stripe:' + account + ':' + str(parent)
            saved = self.store.db.execute("SELECT data FROM money_events WHERE brand=? AND economic_id=? AND kind='payment'", (profile['brand']['url'], parent_id)).fetchone()
            if not saved:
                return {'state': 'waiting_parent_payment', 'reason': 'Original attributed payment has not been verified.'}
            payment = json.loads(saved[0])
            if payment.get('attribution', {}).get('run_id') != run_id:
                raise WorkflowError('退款不屬於這個工作原付款。', 'scope_mismatch')
            row = {'kind': 'refund', 'economic_id': 'stripe:' + account + ':' + str(obj['id']),
                'payment_economic_id': parent_id, 'amount_minor': obj.get('amount')}
        else:
            return {'state': 'ignored', 'reason': 'No supported settled monetary event; Checkout/charge duplicates are not counted as another payment.'}
        stamp = event.get('created')
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 or stamp > self.store.clock() + 300:
            raise ValueError('Invalid original monetary event time')
        payment_ref=row.get('payment_economic_id',row['economic_id']).rsplit(':',1)[-1]
        row.update(currency=str(obj.get('currency', '')).upper(), occurred_at=stamp, verified=True,
            source='stripe_webhook:' + source_id, evidence=['https://dashboard.stripe.com/payments/' + payment_ref],
            attribution={'run_id': run_id, 'channel': config['channel'], 'account_ref': config['account_ref']})
        result = self.ledger.add(profile['brand']['url'], row)
        return {'state': 'applied', 'kind': row['kind'], 'economic_id': row['economic_id'],
            'ledger_event_id': result['event_id'], 'external_customer': row.get('external_customer'),
            'full_costs_complete': False, 'refund_feed_complete': False,
            'note': 'One signed event does not establish complete acquisition, refund, cost or bank-payout coverage.'}
