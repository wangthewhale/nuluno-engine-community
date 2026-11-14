"""Portable commercial decisions; no network, execution grants or invented sales.

The host owns source registries, scope and rights. Model-produced source URLs or
actor labels are proposals, not proof. Source-bound offers and forecasts do not
establish market effect. Economic adoption remains in ``Learning``.
"""
import math

from .files import digest

SCHEMA = 'nuluno-commercial-v1'
ACTORS = {'human', 'buyer_ai'}
VARIABLES = {'audience', 'angle', 'channel', 'followup', 'content_format', 'query', 'purchase_flow', 'graph_method', 'skill_method'}
PROTECTED = {'account_ref', 'account_id', 'tenant_id', 'brand_id', 'product_id', 'budget', 'budget_limit', 'goal', 'authorization', 'grant', 'deadline', 'success_definition', 'payment_receipt'}


def _number(value, minimum=None, maximum=None):
    return type(value) in (int, float) and math.isfinite(value) and (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _scope(value):
    if not isinstance(value, dict) or any(not _text(value.get(k)) for k in ('tenant_id', 'brand_id', 'product_id')):
        raise ValueError('Complete host-bound tenant, brand and product scope required')
    return {k: value[k] for k in ('tenant_id', 'brand_id', 'product_id')}


def _now(now):
    if not _number(now, 0):
        raise ValueError('A finite host clock is required')


def _json(value):
    """Reject non-JSON/NaN payloads before hashing or writing state."""
    import json
    return json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
def _protected_paths(value, prefix=''):
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            path = prefix + '/' + key
            if key in PROTECTED:
                found.append(path)
            found.extend(_protected_paths(item, path))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found.extend(_protected_paths(item, prefix + '/' + str(i)))
    return found


def validate_executable_commercial_graph(method, constraints):
    """Validate new declarative methods without executing generated code.

    Graphs may compose host-known executors in new acyclic orders. Mandatory
    checks must be ancestors of every write. Validation is not canary success.
    """
    method, constraints = _json(method), _json(constraints)
    _scope(method)
    if any(method[k] != constraints.get(k) for k in ('tenant_id', 'brand_id', 'product_id')):
        raise ValueError('Method scope differs from host constraints')
    if not _text(method.get('id')) or not _text(method.get('hypothesis')) or not method.get('source_refs'):
        raise ValueError('Method identity, hypothesis and evidence basis required')
    if method.get('scope_digest') != constraints.get('scope_digest') or not _text(method.get('scope_digest')):
        raise ValueError('Original authority digest must be pinned')
    kind = method.get('kind')
    if kind == 'strategy':
        changes = method.get('changes', {})
        allowed = set(constraints.get('allowed_variables', [])) & VARIABLES
        if not isinstance(changes, dict) or not changes or set(changes) - allowed:
            raise ValueError('Strategy changes exceed the allowed variables')
        if _protected_paths(changes):
            raise ValueError('Method cannot change identity, goal, budget or authority')
    elif kind in ('graph', 'skill'):
        nodes = method.get('nodes')
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= constraints.get('max_nodes', 50):
            raise ValueError('Bounded executable graph nodes required')
        allowed = constraints.get('executors', {})
        seen, ancestors = set(), {}
        mandatory = set(constraints.get('mandatory_before_write', []))
        for node in nodes:
            if not isinstance(node, dict) or set(node) - {'id', 'executor_id', 'arguments', 'depends_on'}:
                raise ValueError('Unsupported executable node fields')
            ident, executor = node.get('id'), node.get('executor_id')
            deps = node.get('depends_on', [])
            if not _text(ident) or ident in seen or executor not in allowed or not isinstance(deps, list) or set(deps) - seen:
                raise ValueError('Nodes require unique IDs, known executors and preceding dependencies')
            if len(deps) != len(set(deps)) or _protected_paths(node.get('arguments', {})):
                raise ValueError('Duplicate dependencies or protected scope mutation')
            ancestors[ident] = set(deps)
            for dep in deps:
                ancestors[ident].update(ancestors[dep])
            if allowed[executor].get('mode') == 'write':
                ancestor_executors = {n['executor_id'] for n in nodes if n.get('id') in ancestors[ident]}
                if not mandatory.issubset(ancestor_executors):
                    raise ValueError('Every write must depend on all original mandatory checks')
            seen.add(ident)
        present = {n['executor_id'] for n in nodes}
        if not set(constraints.get('required_executors', [])).issubset(present):
            raise ValueError('Graph cannot remove original required observation or execution stages')
        writes = sum(allowed[n['executor_id']].get('mode') == 'write' for n in nodes)
        if type(constraints.get('maximum_writes', 1)) is not int or writes > constraints.get('maximum_writes', 1):
            raise ValueError('Graph exceeds the original bounded write count')
    else:
        raise ValueError('Only declarative strategies, skills and graphs are supported; generated shell/Python is not executed')
    return {'schema': SCHEMA, 'method_id': method['id'], 'method_sha256': digest(method),
            'state': 'structurally_valid_candidate', 'requires_independent_trial': True,
            'external_execution_authorized': False, 'market_effect_verified': False}




def validate_commercial_outcome_evidence(observations, scope, trusted_receipts):
    """Check actor and original receipt binding before existing economic gates.

    Requires original unit-level receipts; a user-agent or model actor guess is
    insufficient. Host receipt registries are private configuration/state.
    Aggregate money, uncertainty and adoption are still checked by Learning.
    """
    expected = _scope(scope)
    observations = _json(observations)
    seen, seen_units, reasons = set(), set(), []
    from .learning import COSTS
    for arm in ('baseline', 'candidate'):
        sample = observations.get(arm, {})
        actor, receipts = sample.get('actor'), sample.get('receipt_ids')
        if actor not in ACTORS:
            reasons.append(arm + ':unknown_buyer_actor')
        if not isinstance(receipts, list) or not receipts:
            reasons.append(arm + ':missing_original_receipts')
            continue
        originals = []
        for ident in receipts:
            if not _text(ident) or ident in seen:
                reasons.append(arm + ':duplicate_receipt')
                continue
            seen.add(ident)
            receipt = trusted_receipts.get(ident)
            if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
                reasons.append(arm + ':receipt_scope_mismatch')
                continue
            originals.append(receipt)
            if receipt.get('actor') != actor or receipt.get('actor_binding') != 'authenticated_purchase_context':
                reasons.append(arm + ':unbound_buyer_actor')
            if receipt.get('source_class') not in ('payment_processor', 'product_backend') or not _text(receipt.get('raw_sha256')):
                reasons.append(arm + ':unverified_receipt_origin')
            if receipt.get('test_mode') is not False or receipt.get('owner_purchase') is not False:
                reasons.append(arm + ':test_owner_or_unknown_purchase')
            if receipt.get('currency') != sample.get('currency'):
                reasons.append(arm + ':receipt_currency_mismatch')
            if (not _number(receipt.get('observed_at')) or not _number(sample.get('start_at')) or not _number(sample.get('end_at'))
                    or not sample.get('start_at', math.inf) <= receipt.get('observed_at', -math.inf) < sample.get('end_at', -math.inf)):
                reasons.append(arm + ':receipt_outside_window')
            if any(receipt.get(k) is not True for k in ('complete', 'conversion_delay_complete')) or receipt.get('attribution') != 'comparable':
                reasons.append(arm + ':original_receipt_incomplete_or_unattributed')
        for field in ('receipts', 'refunds', 'reserves'):
            values = [r.get(field) for r in originals]
            if not values or not _number(sample.get(field), 0) or any(not _number(v, 0) for v in values):
                reasons.append(arm + ':unknown_original_' + field)
            elif not math.isclose(sample[field], sum(values), rel_tol=1e-12, abs_tol=1e-9):
                reasons.append(arm + ':original_' + field + '_mismatch')
        for field in COSTS:
            values = [r.get('costs', {}).get(field) for r in originals]
            amount = sample.get('costs', {}).get(field)
            if not values or not _number(amount, 0) or any(not _number(v, 0) for v in values):
                reasons.append(arm + ':unknown_original_cost:' + field)
            elif not math.isclose(amount, sum(values), rel_tol=1e-12, abs_tol=1e-9):
                reasons.append(arm + ':original_cost_mismatch:' + field)
        customers = sample.get('customers')
        units = {r.get('unit_id') for r in originals}
        if None in units or '' in units or type(customers) is not int or customers != len(units):
            reasons.append(arm + ':independent_unit_count_mismatch')
        if seen_units & units:
            reasons.append('same_unit_in_both_arms')
        seen_units.update(units)
        if 'customer_units' in sample:
            grouped={}
            for original in originals:
                costs=original.get('costs',{})
                values=[original.get(k) for k in ('receipts','refunds','reserves')]+[costs.get(k) for k in COSTS]
                if all(_number(v,0) for v in values):
                    unit=original.get('unit_id');grouped[unit]=grouped.get(unit,0)+values[0]-sum(values[1:])
            rows=sample['customer_units']
            if not isinstance(rows,list) or len(rows)!=len(grouped) or {r.get('id') for r in rows if isinstance(r,dict)}!=set(grouped):
                reasons.append(arm+':original_customer_units_mismatch')
            else:
                for unit in rows:
                    if not _number(unit.get('net')) or not math.isclose(unit['net'],grouped[unit['id']],rel_tol=1e-12,abs_tol=1e-9):
                        reasons.append(arm+':original_unit_net_mismatch')
    return {'schema': SCHEMA, 'eligible_for_existing_economic_comparison': not reasons,
            'reasons': sorted(set(reasons)), 'observations_sha256': digest(observations),
            'causality_claim': False, 'sales_success_inferred': False}


class CommercialLearning:
    """Scoped durable adapter; no parallel worker/queue or execution bypass."""
    def __init__(self, store, config=None):
        self.store, self.config = store, config or {}

    def scope(self, run_id):
        profile = self.store.config(run_id)
        return _scope({'tenant_id': self.config.get('tenant_id'), 'brand_id': profile['brand']['url'],
                       'product_id': profile.get('product_id') or profile['brand']['url']})

    def compile_offer(self, run_id, product):
        scope = self.scope(run_id)
        if _scope(product) != scope:
            raise ValueError('Offer belongs to a different tenant, brand or product')
        result = compile_commercial_offer(product, self.store.clock(), self.config.get('trusted_sources', {}))
        self.store.artifact(run_id, 0, 'commercial.offer.' + result['digest'], result)
        self.store.artifact(run_id, 0, 'commercial.offer', result)
        self.store.event(run_id, None, 'commercial_offer_compiled', {'offer_sha256': result['digest'], 'offer_complete': result['offer_complete']})
        return result

    def compare(self, run_id, experiment_id, observations, worker=None):
        from .learning import Learning, COSTS
        learning = Learning(self.store)
        row = self.store.db.execute('SELECT body FROM experiments WHERE brand=? AND id=?', (self.scope(run_id)['brand_id'], experiment_id)).fetchone()
        if not row:
            raise ValueError('No original preregistered commercial experiment for this brand')
        import json
        plan = json.loads(row['body'])
        from .trust import comparison_context
        if self.store.config(run_id).get('mode')!='fixture':
            if plan.get('run_id')!=run_id:return {'decision':'wait','reasons':['original_commercial_run_binding_required'],'causality_claim':False}
            current=comparison_context(self.store,worker or {},run_id)
            if current is None or plan.get('host_context_digest')!=digest(current):
                return {'decision':'wait','reasons':['original_host_model_tools_permission_budget_not_pinned_or_changed'],'causality_claim':False}
        expected_scope = self.scope(run_id)
        actor = plan.get('commercial_actor')
        if actor not in ACTORS or any(plan.get(k) != v for k, v in expected_scope.items()):
            return {'decision': 'wait', 'reasons': ['preregistered_commercial_actor_and_scope_required'], 'causality_claim': False}
        if any(observations.get(arm, {}).get('actor') != actor for arm in ('baseline', 'candidate')):
            return {'decision': 'wait', 'reasons': ['preregistered_actor_mismatch'], 'causality_claim': False}
        if not profile_fixture(self.store,run_id):
            from .economics import Economics
            financial=Economics(self.store,worker or {}).summary(run_id)
            reasons=[]
            if not financial['all_costs_verified'] or not financial['fulfillment_coverage_complete'] or any(not financial['money'][k]['coverage_complete'] for k in ('payment','refund','reserve')):
                reasons.append('original_complete_cost_refund_fulfillment_coverage_required')
            if financial['period']['start']!=plan.get('start_at') or financial['period']['end']!=plan.get('end_at'):reasons.append('original_financial_period_mismatch')
            factor=self.store.config(run_id).get('budget',{}).get('minor_factor',100)
            if financial['all_costs_verified']:
                values=[observations.get(a,{}).get('costs',{}).get(c) for a in ('baseline','candidate') for c in COSTS]
                if not all(_number(v,0) for v in values) or not math.isclose(sum(values)*factor,financial['full_cost_minor'],abs_tol=1e-6):reasons.append('observed_costs_do_not_reconcile_original_ledger')
            for field,kind in [('receipts','payment'),('refunds','refund'),('reserves','reserve')]:
                amounts=[observations.get(a,{}).get(field) for a in ('baseline','candidate')];actual=financial['money'][kind]['amount_minor']
                if actual is not None and (not all(_number(v,0) for v in amounts) or not math.isclose(sum(amounts)*factor,actual,abs_tol=1e-6)):reasons.append('original_ledger_'+field+'_mismatch')
            for arm in ('baseline','candidate'):
                for ident in observations.get(arm,{}).get('receipt_ids',[]):
                    original=self.config.get('trusted_receipts',{}).get(ident,{})
                    if original.get('run_id')!=run_id or original.get('experiment_id')!=experiment_id or original.get('method_version')!=plan.get(arm) or original.get('comparison_context_digest')!=digest(plan['comparison_context']) or not _number(original.get('assigned_at')) or original['assigned_at']>plan['start_at']:
                        reasons.append(arm+':original_prospective_method_assignment_required')
            if reasons:return {'decision':'wait','reasons':sorted(set(reasons)),'causality_claim':False}
        checked = validate_commercial_outcome_evidence(observations, self.scope(run_id), self.config.get('trusted_receipts', {}))
        if not checked['eligible_for_existing_economic_comparison']:
            return {'decision': 'wait', **checked}
        if observations['baseline']['actor'] != observations['candidate']['actor']:
            return {'decision': 'wait', 'reasons': ['comparison_actor_mismatch'], 'causality_claim': False}
        result = learning.compare(run_id, experiment_id, observations)
        self.store.artifact(run_id, 0, 'commercial.comparison.' + experiment_id, {'actor': observations['baseline']['actor'], **checked, **result})
        return result

    def register(self, run_id, body, worker=None):
        from .learning import Learning
        body = _json(body)
        if 'run_id' in body and body['run_id']!=run_id:raise ValueError('Commercial experiment run mismatch')
        body['run_id']=run_id
        expected_scope = self.scope(run_id)
        if body.get('commercial_actor') not in ACTORS or any(k in body and body[k] != v for k, v in expected_scope.items()):
            raise ValueError('Prospective commercial actor and original scope required')
        context = body.get('comparison_context', {})
        if not isinstance(context, dict) or any(not _text(context.get(k)) for k in ('model', 'initial_tools_digest', 'permission_digest', 'budget_digest')):
            raise ValueError('Matched original model, initial tools, permissions and budget context required')
        from .trust import comparison_context
        pinned=comparison_context(self.store,worker or {},run_id)
        if self.store.config(run_id).get('mode')!='fixture' and (pinned is None or any(context.get(k)!=v for k,v in pinned.items())):
            raise ValueError('Comparison context must match original host model, tool, permission and budget digests')
        if pinned:body['host_context_digest']=digest(pinned)
        return Learning(self.store).preregister(run_id, {**body, **expected_scope})

    def next_experiment(self,run_id,body,worker):
        """Persist a proposal using existing ranking/design contracts; no dispatch."""
        from .trust import comparison_context
        from .economics import Economics
        body=_json(body);scope=self.scope(run_id)
        if any(k in body and body[k]!=v for k,v in scope.items()):raise ValueError('Experiment proposal scope mismatch')
        profile=self.store.config(run_id);budget=profile.get('budget',{});factor=budget.get('minor_factor',100)
        known=Economics(self.store,worker).summary(run_id)['full_cost_minor'];cap=budget.get('total_minor')
        remaining=0 if cap==0 else max(0,cap-known)/factor if type(cap) is int and known is not None else None
        prepared={**body,**scope,'budget':{'remaining':remaining,'currency':budget.get('currency')}}
        pinned=comparison_context(self.store,worker,run_id)
        ranking=rank_commercial_actions(prepared,self.store.clock())
        refs=set(self.config.get('trusted_sources',{}))
        import json
        for row in self.store.db.execute("SELECT key,data FROM artifacts WHERE run_id=? AND (kind LIKE 'public_source.%' OR kind LIKE 'flow_output.%')",(run_id,)):
            refs.add(row['key']);value=json.loads(row['data'])
            if isinstance(value,dict) and isinstance(value.get('url'),str):refs.add(value['url'])
        unsupported=sorted({ref for a in prepared.get('actions',[]) for ref in a.get('source_refs',[]) if ref not in refs})
        reasons=[]
        if unsupported:reasons.append('actual_original_action_sources_required')
        if pinned is None:reasons.append('host_model_identity_required')
        if not isinstance(prepared.get('experiment'),dict):reasons.append('prospective_experiment_definition_required')
        result={'schema':SCHEMA,'status':'needs_data','ranking':ranking,'reasons':reasons,'unsupported_source_refs':unsupported,'host_context':pinned,'execution_authorized':False,'commercial_improvement_proven':False}
        if not reasons:
            experiment=prepared['experiment'];context=experiment.get('comparison_context',{})
            if any(context.get(k)!=v for k,v in pinned.items()):raise ValueError('Proposed experiment changes original execution context')
            result={**result,**design_commercial_experiment(prepared,self.store.clock())}
        ident=digest({'scope':scope,'proposal':prepared,'context':pinned})
        self.store.artifact(run_id,0,'commercial.next_experiment.'+ident,{**result,'proposal_id':ident,'created_at':self.store.clock()})
        return {**result,'proposal_id':ident,'new_platform_actions':False}

    def adopt(self,run_id,experiment_id,worker):
        from .learning import Learning
        import json
        learning=Learning(self.store);brand=self.scope(run_id)['brand_id']
        row=self.store.db.execute('SELECT body,result FROM experiments WHERE brand=? AND id=?',(brand,experiment_id)).fetchone()
        if not row or not row['result']:return {'decision':'wait','economic_adopted':False,'reasons':['original_commercial_comparison_required']}
        result=json.loads(row['result']);plan=json.loads(row['body'])
        checked=self.compare(run_id,experiment_id,result.get('observations',{}),worker)
        if checked.get('decision')!='adopt':return {'decision':'wait','economic_adopted':False,'reasons':checked.get('reasons',[]),'comparison':checked}
        head=self.store.db.execute('SELECT version FROM method_heads WHERE brand=?',(brand,)).fetchone()
        saved=self.store.artifact(run_id,0,'commercial.adoption.'+experiment_id)
        if head and head['version']==plan['candidate'] and saved:
            return {**saved,'already_adopted':True}
        adopted=learning.adopt(run_id,experiment_id)
        receipt={'decision':'adopted','economic_adopted':True,'fixture_only':profile_fixture(self.store,run_id),'experiment_id':experiment_id,
            'version':plan['candidate'],'original_evidence_digest':checked.get('evidence_digest'),'dispatch':adopted,'model_weights_updated':False}
        self.store.artifact(run_id,0,'commercial.adoption.'+experiment_id,receipt)
        return receipt

    def monitor(self,run_id,experiment_id,observations,worker):
        from .learning import Learning
        checked=self.compare(run_id,experiment_id,observations,worker)
        if checked.get('decision')=='wait':return {'decision':'wait','comparison':checked,'rollback_executed':False}
        return Learning(self.store).monitor(run_id,experiment_id,observations)

    def transfer_register(self,run_id,body,worker):
        from .learning import Learning
        import json
        body=_json(body)
        if set(body)!={'method_id','experiment'}:raise ValueError('Held-out transfer accepts a host method ID and prospective experiment only')
        method=self.config.get('transfer_methods',{}).get(body['method_id'])
        if not isinstance(method,dict):raise ValueError('Original adopted method must be installed by the host')
        target={**self.scope(run_id),'product_category':self.store.config(run_id).get('product_category'),'actor':body['experiment'].get('commercial_actor')}
        training=method.get('training_product_ids')
        if not isinstance(training,list) or not training or target['product_id'] in training or method.get('product_id')==target['product_id']:
            raise ValueError('Held-out target must not have participated in learning')
        allowed=self.transfer(run_id,method,target)
        if not allowed.get('allowed'):raise ValueError('Current original transfer rights and applicability required')
        experiment=_json(body['experiment']);brand=self.scope(run_id)['brand_id'];learning=Learning(self.store)
        candidate=self.store.db.execute('SELECT body FROM method_versions WHERE brand=? AND id=?',(brand,experiment.get('candidate'))).fetchone()
        if not candidate or json.loads(candidate['body']).get('changes')!=method.get('changes'):raise ValueError('Target candidate must use the original transferred method')
        if self.store.db.execute('SELECT 1 FROM operations WHERE task_key IN (SELECT key FROM tasks WHERE run_id=?)',(run_id,)).fetchone():raise ValueError('Held-out assignment must precede target external execution')
        registered=self.register(run_id,experiment,worker)
        ident=digest({'method':method,'target':target,'experiment_id':registered['experiment_id']})
        saved={'trial_id':ident,'method_id':body['method_id'],'source_method_digest':digest(method),'training_product_ids':training,
            'target_scope':target,'experiment_id':registered['experiment_id'],'registered_at':self.store.clock(),'status':'prospective_trial','private_customer_data_transferred':False}
        self.store.artifact(run_id,0,'commercial.transfer_trial.'+ident,saved)
        return saved

    def transfer_compare(self,run_id,trial_id,observations,worker):
        trial=self.store.artifact(run_id,0,'commercial.transfer_trial.'+trial_id)
        if not trial:raise ValueError('Original prospective held-out trial required')
        method=self.config.get('transfer_methods',{}).get(trial['method_id'])
        if not method or digest(method)!=trial['source_method_digest']:raise ValueError('Original source method changed')
        allowed=self.transfer(run_id,method,trial['target_scope'])
        if not allowed.get('allowed'):return {'decision':'wait','reasons':['current_transfer_rights_required'],'heldout_benefit_verified':False}
        compared=self.compare(run_id,trial['experiment_id'],observations,worker)
        real=compared.get('decision')=='adopt' and not profile_fixture(self.store,run_id)
        result={'trial_id':trial_id,'comparison':compared,'decision':compared.get('decision'),'heldout_benefit_verified':real,
            'requires_separate_adoption':True,'private_customer_data_transferred':False,'causality_claim':False}
        self.store.artifact(run_id,0,'commercial.transfer_result.'+trial_id,result)
        return result

    def transfer(self, run_id, method, target):
        if _scope(target) != self.scope(run_id):
            raise ValueError('Transfer target is outside this run scope')
        grants = self.config.get('transfer_rights', [])
        if not isinstance(grants, list):
            raise ValueError('Transfer grants must come from host configuration')
        result = decide_commercial_transfer(method, {}, target, self.store.clock())
        for grant in grants:
            checked = decide_commercial_transfer(method, grant, target, self.store.clock())
            if checked['allowed']:
                result = checked
                break
        self.store.artifact(run_id, 0, 'commercial.transfer.' + digest(result), result)
        return result


# The same pure JSON contracts are used by the native Nuluno service.
def profile_fixture(store,run_id):
    return store.config(run_id).get('mode')=='fixture'

from .commercial_contract import (
    compileCommercialOffer, rankCommercialActions, designCommercialExperiment,
    validateCommercialMethod, decideCommercialTransfer,
    compile_commercial_offer, rank_commercial_actions, design_commercial_experiment,
    validate_commercial_method, decide_commercial_transfer,
)
