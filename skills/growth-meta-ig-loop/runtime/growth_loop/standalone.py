"""Create an explicit operator-owned service scope without Nuluno.com."""
import os
import re
from pathlib import Path
from urllib.parse import urlparse
from .files import atomic_json


def read_public_source(store, settings, run_id, url, compact=False, request_id=None):
    """Exact read-only product source route; no arbitrary tool or action input."""
    from .tooling import fetch_public
    from .files import digest
    from .errors import WorkflowError
    profile = store.config(run_id)
    route = settings.get('tool_routes', {}).get('public-source', {})
    if route.get('enabled') is not True or route.get('transport') != 'builtin_public_web' or route.get('mode') != 'read' or route.get('tool_name') != 'fetch_public' or profile['brand']['url'] not in route.get('brand_urls', []) or route.get('fixture_only') is True:
        raise WorkflowError('No operator-pinned live public read route for this product.', 'scope_mismatch')
    if 'allowed_urls' in route and (not isinstance(route['allowed_urls'],list) or url not in route['allowed_urls']):
        raise WorkflowError('Source URL is outside the original exact URL allowlist.','scope_mismatch')
    import json,uuid
    from .files import canonical
    from .public_sources import compact_public_source
    if (store.root/'.transfer.lock').exists():raise WorkflowError('Workspace is frozen for transfer.','waiting_capability')
    policy=settings.get('public_read_policy',{})
    limit=policy.get('read_limit')
    maximum=policy.get('max_response_bytes',8192)
    compact_bytes=policy.get('compact_bytes',2500)
    if limit is not None and (type(limit) is not int or not 1<=limit<=10000):raise ValueError('Invalid public read limit')
    if type(maximum) is not int or not 2048<=maximum<=65536:raise ValueError('Invalid response byte limit')
    request_id=request_id or uuid.uuid4().hex
    if not re.fullmatch('[A-Za-z0-9_.-]{1,128}',request_id):raise ValueError('Bounded stable public read request_id required')
    store.db.execute('''CREATE TABLE IF NOT EXISTS public_read_requests(run_id TEXT NOT NULL,id TEXT NOT NULL,binding TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL,updated REAL NOT NULL,response TEXT,PRIMARY KEY(run_id,id))''')
    binding=digest({'url':url,'route':route,'compact':compact,'compact_bytes':compact_bytes,'max_response_bytes':maximum})
    with store.transaction():
        old=store.db.execute('SELECT * FROM public_read_requests WHERE run_id=? AND id=?',(run_id,request_id)).fetchone()
        if old and old['binding']!=binding:raise ValueError('Same public read request changed')
        if old and old['state']=='completed':return {**json.loads(old['response']),'reused_original_receipt':True}
        if old and old['state']=='dispatching' and store.clock()-old['updated']<130:raise WorkflowError('Original public read is still in flight.','transient')
        spent=store.db.execute('SELECT COALESCE(SUM(attempts),0) FROM public_read_requests WHERE run_id=?',(run_id,)).fetchone()[0]
        if limit is not None and spent>=limit:raise WorkflowError('Original public read quota is exhausted; preserve saved receipts.','waiting_approval')
        store.db.execute('INSERT INTO public_read_requests VALUES(?,?,?,?,?,?,NULL) ON CONFLICT(run_id,id) DO UPDATE SET state=excluded.state,attempts=public_read_requests.attempts+1,updated=excluded.updated',
            (run_id,request_id,binding,'dispatching',1,store.clock()))
    try:value = fetch_public(url, route.get('allowed_hosts', []), False)
    except BaseException:
        store.db.execute("UPDATE public_read_requests SET state='failed',updated=? WHERE run_id=? AND id=?",(store.clock(),run_id,request_id));raise
    receipt = {'run_id': run_id, 'url': url, 'observed_at': store.clock(),
        'classification': 'controlled_fixture' if profile.get('mode')=='fixture' else 'live_public_source', 'external_writes': 0,
        'operator_route_sha256': digest(route), 'result': value,
        'effectiveness_or_purchase_verified': False}
    receipt_id = digest(receipt)
    store.artifact(run_id, 0, 'public_source.'+receipt_id, receipt)
    response={**receipt,'receipt_id':receipt_id,'request_id':request_id,'reused_original_receipt':False}
    if compact:
        response['result']=compact_public_source(value,compact_bytes)
        framed={'content':[{'type':'text','text':canonical(response)}],'structuredContent':response,'isError':False}
        if len(canonical(framed).encode('utf-8'))>maximum-256:raise ValueError('MCP response exceeds configured byte limit; reduce compact_bytes')
    store.db.execute("UPDATE public_read_requests SET state='completed',updated=?,response=? WHERE run_id=? AND id=?",(store.clock(),canonical(response),run_id,request_id))
    return response


def delivery_report(store, run_id, settings=None):
    """Read persisted actual deliverables and original receipts for one run."""
    import json
    from .files import redact
    from .learning import Learning
    from .economics import Economics
    from .observability import ExecutionMetrics
    settings=settings or {}
    economics=Economics(store,settings).summary(run_id)
    profile = store.config(run_id)
    artifacts = [{'kind': r['kind'], 'cycle': r['cycle'], 'data': json.loads(r['data'])}
        for r in store.db.execute("SELECT kind,cycle,data FROM artifacts WHERE run_id=? AND (kind LIKE 'flow_output.%' OR kind LIKE 'public_source.%' OR kind LIKE 'tool.execution.%') ORDER BY created,rowid", (run_id,))]
    operations = [{'state': r['state'], 'count': r['n']} for r in store.db.execute("SELECT state,count(*) n FROM operations WHERE task_key IN (SELECT key FROM tasks WHERE run_id=?) GROUP BY state", (run_id,))]
    return redact({'run_id': run_id, 'brand_url': profile['brand']['url'], 'status': store.status(run_id),
        'deliverables_and_read_receipts': artifacts, 'saved_reviews': Learning(store).search(run_id),
        'usage': {'persisted_source_reads': sum(r['kind'].startswith('public_source.') for r in artifacts),
            'persisted_tool_executions': sum(r['kind'].startswith('tool.execution.') for r in artifacts),
            'external_operations_by_state': operations,
            'model_invoice_cost': None, 'hosting_cost': None, 'full_cost': economics['full_cost_minor'],
            'currency':economics['currency'],'cost_unit':'integer_minor_currency_units',
            'unknown_costs_are_not_zero': True},
        'economics':economics,'execution_metrics':ExecutionMetrics(store,settings).summary(run_id),
        'sales_outcome_verified': False, 'report_is_not_incremental_sales_evidence': True})


def initialize_service(workspace, config_path, brand_url, run_id, worker='external', token_env='ENGINE_EXTERNAL_AGENT_TOKEN', brand_name=None):
    url = urlparse(brand_url)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use the real public HTTPS product URL, without credentials or query.')
    for label, value in [('run_id', run_id), ('worker', worker)]:
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', value):
            raise ValueError('Invalid '+label)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{2,95}', token_env):
        raise ValueError('Use an explicit token environment variable name.')
    root, path = Path(workspace).resolve(), Path(config_path).resolve()
    if path.exists():
        raise ValueError('Service config already exists; preserve its original scope and edit it explicitly.')
    root.mkdir(parents=True, exist_ok=True); os.chmod(root, 0o700)
    profile = {'mode': 'prepare', 'brand': {'name': brand_name or url.hostname, 'url': brand_url, 'facts': {}},
        'workflow': {'id': 'single', 'skill_id': 'S01', 'max_iterations': 1, 'observation_seconds': 3600},
        'budget': {'currency': 'USD', 'minor_factor': 100, 'daily_minor': 0, 'total_minor': 0},
        'bindings': {}, 'input': {'requested_outcome': 'Verify current product facts, prices, delivery terms and missing evidence from public sources.'}}
    route = {'enabled': True, 'transport': 'builtin_public_web', 'mode': 'read', 'channel': 'web',
        'brand_urls': [brand_url], 'skill_ids': ['S01'], 'capabilities': ['web.read'],
        'tool_name': 'fetch_public', 'allowed_hosts': [url.hostname],
        'probe': {'read_only': True, 'tool_name': 'fetch_public', 'arguments': {'url': brand_url}}}
    import uuid
    config = {'workers': {worker: {'token_env': token_env, 'runs': [run_id], 'brand_urls': [brand_url],
        'allow_run_create': True, 'capabilities': ['web.read', 'local.plan', 'agent.write', 'local.review'],
        'enforce_preflight': True, 'verification_max_age': 3600,
        'allow_evolution': False, 'allow_goal_continuity': False,
        'commercial':{'tenant_id':'operator-'+uuid.uuid4().hex},
        'comparison_models':{},'economics':{'sources':{},'trusted_receipts':{},'coverage':{}},
        'tool_routes': {'public-source': route}}}}
    atomic_json(path, config, private=True)
    atomic_json(root/'initial-profile.json', profile, private=True)
    return {'product': 'Nuluno Engine', 'workspace': str(root), 'config': str(path),
        'worker': worker, 'run_id': run_id, 'token_env': token_env,
        'token_generated': False, 'platform_authority': False, 'nuluno_com_required': False,
        'profile': profile, 'next': 'Set a unique 32+ character token in the named environment variable; start the independent MCP gateway; call run_create, tool_probe, preflight, acquire, tool_execute, complete.'}
