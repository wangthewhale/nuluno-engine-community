"""Enforced channel-specific work contracts and bounded recovery, sharing S01–S34."""
import json
import math
import re
import copy
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
from .contracts import _proof
from .demand import qualify
from .errors import Waiting, WorkflowError
from .files import canonical, digest

from .resources import skill_root
REF=skill_root()/'references'

def channel_catalog():
    return json.loads((REF/'channel-catalog.json').read_text(encoding='utf-8'))

@lru_cache(maxsize=2)
def _skill_index(path, modified_ns, size):
    rows=json.loads(Path(path).read_text(encoding='utf-8'))['channel_skills']
    return {key:row for row in rows for key in (row['id'],row['name'])}

def channel_skill(ident):
    path=REF/'channel-catalog.json';stamp=path.stat()
    row=_skill_index(str(path),stamp.st_mtime_ns,stamp.st_size).get(ident)
    if row is None:raise ValueError('Unknown channel skill: '+str(ident))
    return copy.deepcopy(row)  # One caller cannot mutate another brand's contract.


def validate_channel_receipt(store, task, receipt):
    ident=task['data'].get('channel_skill_id')
    if not ident:return
    spec=channel_skill(ident);profile=store.config(task['run_id']);mode=profile.get('mode','prepare')
    checks=receipt.get('channel_step_checks',{})
    if set(checks)!={s['id'] for s in spec['steps']}:
        raise WorkflowError('需逐項回報本渠道所有細步驟。','invalid_result')
    for step in spec['steps']:
        check=checks[step['id']]
        scoped_na=isinstance(check,dict) and check.get('status')=='not_applicable' and profile.get('workflow',{}).get('id')=='goal' and spec['parent_skill_id']=='S33' and isinstance(check.get('reason'),str) and bool(check['reason'].strip())
        if not isinstance(check,dict) or check.get('status') not in ('done','waiting') and not scoped_na:
            raise WorkflowError('渠道步驟需done或waiting；讀取適用條件也須有證據。','invalid_result')
        proof=check.get('evidence',[])
        if not isinstance(proof,list) or not proof or any(not isinstance(p,dict) or not _proof(p.get('reference'),mode) or type(p.get('observed_at')) not in (int,float) or not math.isfinite(p['observed_at']) or p['observed_at']>store.clock()+300 for p in proof):
            raise WorkflowError('每個渠道步驟需具來源與時間的結構化讀回。','invalid_result')
        if receipt.get('state','completed')=='completed' and check['status']!='done' and not scoped_na:
            raise WorkflowError('渠道仍有步驟等待，不能標完成。','invalid_result')
    if receipt.get('state','completed')=='completed' and not any(c.get('status')=='done' for c in checks.values()):raise WorkflowError('至少需完成本目標的實際觀測，不能把整份渠道觀測標不適用。','invalid_result')
    if receipt.get('state','completed')!='completed':return
    data=receipt.get('data',{})
    for field in spec['required_data_fields']:
        if field not in data:
            raise WorkflowError('缺少渠道交付欄位：'+field,'invalid_result')
    for field,kind in spec.get('required_data_types',{}).items():
        if type(data.get(field)) is not {'object':dict,'array':list,'string':str}[kind]:
            raise WorkflowError('渠道欄位格式不符：'+field,'invalid_result')
        if kind=='string' and not data[field].strip():raise WorkflowError('渠道文字欄位不能空白：'+field,'invalid_result')
    if spec['parent_skill_id']=='S05' and not any(a.get('channel')==spec['channel'] and a.get('state')=='connected' for a in data.get('accounts',[])):
        raise WorkflowError('本渠道帳號尚未讀回connected，需等待本人或能力。','invalid_result')
    if channel_catalog()['channels'][spec['channel']]['access_policy']=='approved_api_or_user_export' and data.get('access_route') not in ('approved_api','user_export','user_completed_ui'):
        raise WorkflowError('此平台需明確記錄允許的API／合法匯出或本人完成路徑，不使用網站自動抓取。','invalid_result')
    if 'business_contacts' in spec['required_data_fields']:
        for contact in data['business_contacts']:
            email=contact.get('email')
            if email is not None and (not isinstance(email,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or not _proof(contact.get('source_url'),mode) or contact.get('public_business_contact') is not True or not contact.get('intended_purpose')):
                raise WorkflowError('Email需本人公開商務來源與用途，未知保留null。','invalid_result')
            if contact.get('inferred') or contact.get('marketing_consent') not in (None,'unknown'):
                raise WorkflowError('找名單不能猜Email或宣告同意寄送。','scope_mismatch')
    if 'demand_posts' in spec['required_data_fields']:
        allowed={'reddit':{'reddit.com','www.reddit.com'},'threads':{'threads.com','www.threads.com','threads.net','www.threads.net'},'x':{'x.com','www.x.com','twitter.com','www.twitter.com'},'youtube':{'youtube.com','www.youtube.com','youtu.be'}}
        for post in data['demand_posts']:
            if post.get('platform')!=spec['channel'] or urlparse(post.get('url','')).hostname not in allowed.get(spec['channel'],set()):
                raise WorkflowError('公開需求原文不屬於此渠道。','scope_mismatch')
        # Deterministic qualification, not an agent's "looks qualified" flag.
        from .sales_state import SalesState
        SalesState(store)
        history=[json.loads(r[0]).get('signal_id') for r in store.db.execute('SELECT data FROM contacts WHERE brand=?',(profile['brand']['url'],))]
        result=qualify(data['demand_posts'],profile['brand'],store.clock,profile.get('acquisition',{}).get('max_age_days',14),history=[x for x in history if x])
        data['demand_qualification']=result
        data['leads']=[{'channel':spec['channel'],'identity':r['author_id'],'source_url':r['url'],'reason':r['fit_reason'],
                        'signal_id':r['signal_id'],'signal_status':r['status'],'need_quote':r['need_quote'],'fact_ids':r['fact_ids']} for r in result['qualified']]
        data['excluded']=result['excluded']
    if spec['channel']=='audience':validate_audience(data,spec,mode)


def validate_audience(data,spec,mode):
    # Motives are sourced observations or explicit hypotheses, not diagnoses.
    for field in ('voice_records','audience_segments','motive_map','motive_priorities','benefit_ladders','angle_candidates','claim_checks','angle_findings'):
        if field not in spec['required_data_fields']:continue
        if not isinstance(data[field],list):raise WorkflowError(field+'需清單。','invalid_result')
        for row in data[field]:
            if not isinstance(row,dict) or row.get('evidence_level') not in ('verified','hypothesis','unsupported') or not row.get('sources'):
                raise WorkflowError('受眾／動機／角度需來源與證據等級；假設也要列依據來源。','invalid_result')
            sources=row['sources']
            if not isinstance(sources,list) or not all(_proof(s.get('reference') if isinstance(s,dict) else s,mode) for s in sources):
                raise WorkflowError('動機／角度來源需原始URL或可信事件ID。','invalid_result')
            if row.get('sensitive_inference') or row.get('invented_quote'):
                raise WorkflowError('不能造原話或猜敏感個人屬性。','scope_mismatch')
            if row.get('causal_claim') and row['evidence_level']!='verified':
                raise WorkflowError('待驗利益不能寫成提高求職／收入等因果承諾。','invalid_result')
    if 'angle_test' in spec['required_data_fields']:
        plan=data['angle_test']
        if not isinstance(plan,dict) or not all(plan.get(k) for k in ('hypothesis','audience_id','changed_variable','primary_metric','window','stop_rule')) or plan['changed_variable'] not in ('audience','angle','content_format','followup','channel'):
            raise WorkflowError('角度測試需先寫單一變數、受眾、指標、窗口及停止條件。','invalid_result')


RECOVERY={
 'login_required':('waiting_owner','沿原品牌官方登入／OAuth，完成後讀回相同subject；不取出session秘密'),
 'mfa_required':('waiting_owner','保存官方驗證位置，等本人完成；不猜驗證碼'),
 'captcha':('waiting_owner','保留原頁與任務，等本人完成CAPTCHA；不換帳號／proxy繞過'),
 'wrong_identity':('waiting_capability','重新列帳號與核對品牌URL；只切已授權身分'),
 'permission_missing':('waiting_capability','指出缺少scope／管理角色與官方續接入口；不自動擴權'),
 'rate_limited':('retry','依實際Retry-After／reset退避，保留cursor'),
 'ui_changed':('waiting_agent','重讀當前頁面與可用工具schema，依語意重新定位一次；不盲點座標'),
 'upload_format':('waiting_agent','用現有normalize-image／video轉成工具當前支援格式，核對新檔雜湊及視覺'),
 'processing':('retry','保留container／video／ad ID，等狀態更新，不重建'),
 'uncertain_write':('waiting_reconcile','以原operation key、外部ID及全文查回執；未明前禁止重送'),
 'no_data':('retry','等本輪觀察窗口與資料延遲；不把未知當0'),
 'not_eligible':('waiting_owner','保存實際資格及正常申請入口，不虛構本人資料'),
 'rules_disallow':('waiting_capability','停止此渠道動作，保存排除理由；其他獨立分支繼續')}


def recover(store,run_id,task_key,code,evidence,retry_at=None):
    task=store.task(task_key)
    if not task or task['run_id']!=run_id or not task['data'].get('channel_skill_id') or task['state'] in ('done','cancelled','running'):
        raise WorkflowError('只可修復本品牌未完成且無持有租約的渠道節點。','scope_mismatch')
    if store.artifact(run_id,0,'graph.stopped'):raise WorkflowError('流程已停止。','scope_mismatch')
    spec=channel_skill(task['data']['channel_skill_id']);ch=channel_catalog()['channels'][spec['channel']]
    if code in RECOVERY:state,instruction=RECOVERY[code]
    elif code in ch['platform_blockers']:state,instruction='waiting_agent',ch['platform_blockers'][code]
    else:raise ValueError('未知阻礙需保留原始錯誤並人工分類，不能聲稱已解決')
    if not evidence:raise ValueError('修復需要原始錯誤來源')
    mode=store.config(run_id).get('mode','prepare')
    if not isinstance(evidence,list) or any(not isinstance(p,dict) or not _proof(p.get('reference'),mode) for p in evidence):raise ValueError('修復需結構化來源證據')
    pending=store.db.execute("SELECT 1 FROM operations WHERE task_key=? AND state IN ('uncertain','in_flight')",(task_key,)).fetchone()
    if pending:state,instruction=RECOVERY['uncertain_write'];code='uncertain_write'
    history=store.artifact(run_id,task['cycle'],'channel.recovery.'+digest(task_key)) or {'attempts':{}}
    history['attempts'][code]=history['attempts'].get(code,0)+1
    if history['attempts'][code]>store.config(run_id).get('recovery',{}).get('max_attempts',5):state='waiting_owner'
    next_at=0
    if state=='retry':
        if type(retry_at) not in (int,float) or not math.isfinite(retry_at) or retry_at<=store.clock():raise ValueError('退避需實際有效未來重試時間')
        next_at=retry_at
    result={'code':code,'state':state,'instruction':instruction,'evidence':evidence,'attempts':history['attempts'][code],'original_task_id':task_key}
    history['last']=result
    with store.transaction():
        store.artifact(run_id,task['cycle'],'channel.recovery.'+digest(task_key),history)
        store.db.execute('UPDATE tasks SET state=?,next_at=?,outcome=?,lease=NULL,lease_until=NULL WHERE key=?',(state,next_at,canonical({'reason':instruction,'recovery':result,'request_revision':digest(result),'details':{'request_file':(store.artifact(run_id,task['cycle'],'flow_request.'+digest(task_key)[:20]) or {}).get('request_file')}}),task_key))
        store.event(run_id,task_key,'channel_recovery_routed',result)
    return result
