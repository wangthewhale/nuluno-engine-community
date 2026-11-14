"""A moved SQLite file is history, not a new source or permission grant."""
import json
from .files import digest


def comparison_context(store,worker,run_id):
    profile=store.config(run_id)
    model=worker.get('comparison_models',{}).get(run_id)
    if not isinstance(model,str) or not model.strip():return None
    grant=store.root/'authorization.json'
    authority=json.loads(grant.read_text()) if grant.exists() else None
    return {'model':model,'initial_tools_digest':digest(worker.get('tool_routes',{})),
        'permission_digest':digest({'capabilities':worker.get('capabilities',[]),'brand':profile['brand']['url'],'bindings':profile.get('bindings',{}),'authority':authority,'allowed_methods':worker.get('methods'),'revoked':worker.get('revoked',False)}),
        'budget_digest':digest(profile.get('budget',{}))}


def trust_status(store,worker,run_id):
    profile=store.config(run_id)
    sources=worker.get('data_sources',{})
    restored=(store.root/'workspace-origin.json').exists()
    return {'run_id':run_id,'brand_url':profile['brand']['url'],'restored_workspace':restored,
        'commercial_tenant_bound':bool(worker.get('commercial',{}).get('tenant_id')),
        'source_registry_digest':digest(sources),'financial_source_registry_digest':digest(worker.get('economics',{}).get('sources',{})),
        'configured_source_ids':sorted(k for k,v in sources.items() if isinstance(v,dict) and v.get('enabled') is True and run_id in v.get('run_ids',[])),
        'comparison_context':comparison_context(store,worker,run_id),
        'financial_sources_currently_enabled':sum(isinstance(v,dict) and v.get('enabled') is True and run_id in v.get('run_ids',[]) for v in worker.get('economics',{}).get('sources',{}).values()),
        'copied_history_grants_authority':False,'restored_source_receipts_require_rebinding':True,'model_metadata_is_host_attested':True,
        'private_credentials_returned':False}


def learning_status(store,worker,run_id):
    from .observability import ExecutionMetrics
    from .economics import Economics
    from .learning import Learning
    learning=Learning(store);brand=store.config(run_id)['brand']['url']
    head=store.db.execute('SELECT version,previous FROM method_heads WHERE brand=?',(brand,)).fetchone()
    plans=[{'kind':r['kind'],'data':json.loads(r['data'])} for r in store.db.execute("SELECT kind,data FROM artifacts WHERE run_id=? AND (kind LIKE 'commercial.next_experiment.%' OR kind LIKE 'commercial.comparison.%' OR kind LIKE 'commercial.transfer_result.%') ORDER BY created,rowid",(run_id,))]
    return {'run_id':run_id,'trust':trust_status(store,worker,run_id),'metrics':ExecutionMetrics(store,worker).summary(run_id),
        'economics':Economics(store,worker).summary(run_id),'current_economic_method':dict(head) if head else None,
        'saved_commercial_decisions':plans,'weights_updated':False,'sales_lift_inferred':False}
