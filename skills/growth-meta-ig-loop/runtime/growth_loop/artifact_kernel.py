"""Portable declarative evolution kernel. No eval, shell, imports or I/O in programs.

Generated programs compose bounded JSON operations. They cannot create OS code or
credentials. Graph effects are dispatched ONLY by the host's original executor;
this module never performs a marketing action or grants authority.
"""
import copy
import json
import math
from .files import canonical, digest

SCHEMA = 'nuluno-evolution-v1'
OPS = ('identity', 'object', 'pick', 'concat', 'strip', 'lower', 'replace', 'unique',
       'sort', 'filter_equal', 'map_pick', 'coalesce', 'equals', 'add', 'multiply')
CONTEXT = ('model', 'initial_tools_digest', 'permission_digest', 'budget_digest', 'outcome_definition')
PROTECTED = {'tenant_id', 'brand_id', 'product_id', 'account_id', 'account_ref', 'authorization',
             'grant', 'token', 'api_key', 'password', 'budget', 'budget_limit', 'recipient',
             'operation_key', 'deadline', 'success_definition'}


def clone(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > 512 * 1024:
        raise ValueError('evolution_payload_too_large')
    return json.loads(raw)


def text(x):
    return isinstance(x, str) and 0 < len(x) <= 4000


def pointer(value, path):
    if path == '':
        return value
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 1000:
        raise ValueError('json_pointer_required')
    for key in path.split('/')[1:]:
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def expressions(value, preceding, depth=0):
    if depth > 24:
        raise ValueError('expression_depth_limit')
    if isinstance(value, dict):
        if 'ref' in value:
            if set(value) - {'ref', 'path'} or value['ref'] not in preceding or not isinstance(value.get('path', ''), str):
                raise ValueError('reference_must_be_input_or_preceding_step')
        elif set(value) == {'literal'}:
            clone(value['literal'])
        else:
            for item in value.values(): expressions(item, preceding, depth + 1)
    elif isinstance(value, list):
        for item in value: expressions(item, preceding, depth + 1)


def resolve(value, env):
    if isinstance(value, dict):
        if 'ref' in value: return copy.deepcopy(pointer(env[value['ref']], value.get('path', '')))
        if set(value) == {'literal'}: return copy.deepcopy(value['literal'])
        return {k: resolve(v, env) for k, v in value.items()}
    if isinstance(value, list): return [resolve(v, env) for v in value]
    return value


def validate_program(body):
    if not isinstance(body, dict) or set(body) != {'steps', 'output'} or not isinstance(body['steps'], list) or not 1 <= len(body['steps']) <= 64:
        raise ValueError('bounded_dataflow_program_required')
    seen = {'input'}
    for step in body['steps']:
        if not isinstance(step, dict) or set(step) != {'id', 'op', 'args'} or not text(step['id']) or step['id'] in seen or step['op'] not in OPS or not isinstance(step['args'], dict):
            raise ValueError('unknown_program_operation_or_duplicate_step')
        expressions(step['args'], seen)
        seen.add(step['id'])
    expressions(body['output'], seen)
    return body


def apply_operation(op, a):
    # Every operation works on JSON values only. No user expressions, filenames,
    # URLs, subprocesses, network functions or dynamic attribute access exist.
    if op == 'identity': return a['value']
    if op == 'object': return a
    if op == 'pick': return pointer(a['value'], a['path'])
    if op in ('strip', 'lower'):
        if not isinstance(a['value'], str): raise ValueError('string_required')
        return a['value'].strip() if op == 'strip' else a['value'].lower()
    if op == 'replace':
        if not all(isinstance(a[k], str) for k in ('value', 'old', 'new')) or not a['old']: raise ValueError('literal_nonempty_replacement_required')
        if len(a['value']) + a['value'].count(a['old']) * (len(a['new']) - len(a['old'])) > 512*1024: raise ValueError('replacement_output_too_large')
        return a['value'].replace(a['old'], a['new'])
    if op == 'concat':
        if not isinstance(a['values'], list) or not all(isinstance(v, str) for v in a['values']) or not isinstance(a.get('separator', ''), str): raise ValueError('string_list_required')
        if sum(len(v) for v in a['values']) + len(a.get('separator','')) * max(0,len(a['values'])-1) > 512*1024: raise ValueError('concat_output_too_large')
        return a.get('separator', '').join(a['values'])
    if op in ('unique', 'sort', 'filter_equal', 'map_pick'):
        values = a['values']
        if not isinstance(values, list) or len(values) > 10000: raise ValueError('bounded_list_required')
        if op == 'unique': return list({canonical(v): v for v in values}.values())
        if op == 'sort': return sorted(values, key=canonical)
        if op == 'filter_equal': return [v for v in values if pointer(v, a['path']) == a['equals']]
        return [pointer(v, a['path']) for v in values]
    if op == 'coalesce':
        if not isinstance(a['values'], list): raise ValueError('list_required')
        return next((v for v in a['values'] if v is not None), None)
    if op == 'equals': return a['left'] == a['right']
    if op in ('add', 'multiply'):
        values = a['values']
        if not isinstance(values, list) or len(values) > 10000 or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) < 1e100 for v in values): raise ValueError('bounded_finite_numbers_required')
        result = sum(values) if op == 'add' else math.prod(values)
        if not math.isfinite(result): raise ValueError('finite_output_required')
        return result
    raise ValueError('unknown_operation')


def protected_values(value, prefix=''):
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + '/' + key.replace('~', '~0').replace('/', '~1')
            if key.lower() in PROTECTED: result[path] = child
            result.update(protected_values(child, path))
    elif isinstance(value, list):
        for i, child in enumerate(value): result.update(protected_values(child, prefix + '/' + str(i)))
    return result


def run_program(body, arguments, constraints):
    validate_program(body)
    env = {'input': clone(arguments)}
    for step in body['steps']:
        env[step['id']] = clone(apply_operation(step['op'], resolve(step['args'], env)))
    output = clone(resolve(body['output'], env))
    if not isinstance(output, dict): raise ValueError('program_output_must_be_object')
    if constraints.get('argument_transform'):
        # Only explicit public mutable pointers may change; all other original
        # input, including destination/account/budget fields, is byte-equivalent.
        allowed = constraints.get('mutable_pointers', [])
        before, after = clone(arguments), clone(output)
        for path in allowed:
            for value in (before, after):
                parts = path.split('/')[1:]
                if not path.startswith('/') or any(p in PROTECTED for p in parts): raise ValueError('protected_mutable_pointer')
                cursor = value
                for part in parts[:-1]: cursor = cursor[part.replace('~1','/').replace('~0','~')]
                key = parts[-1].replace('~1','/').replace('~0','~')
                if isinstance(cursor, dict): cursor.pop(key, None)
                else: raise ValueError('mutable_arrays_not_supported')
        if before != after: raise ValueError('program_changed_unapproved_input')
    if constraints.get('argument_transform') and protected_values(arguments) != protected_values(output): raise ValueError('protected_identity_or_authority_changed')
    for path in constraints.get('immutable_pointers', []):
        if pointer(arguments, path) != pointer(output, path): raise ValueError('immutable_input_changed')
    return {'output': output, 'steps': len(body['steps']), 'external_effects': 0}


def validate_graph(body, constraints):
    if not isinstance(body, dict) or set(body) != {'nodes', 'output'} or not isinstance(body['nodes'], list) or not 1 <= len(body['nodes']) <= 128:
        raise ValueError('bounded_graph_required')
    nodes = body['nodes']; seen = {'input'}; ancestors = {}; by_id = {}
    executors = constraints.get('executors', {})
    for node in nodes:
        if not isinstance(node, dict) or set(node) - {'id', 'depends_on', 'program', 'executor_id', 'arguments'} or not text(node.get('id')) or node['id'] in seen:
            raise ValueError('graph_node_contract_invalid')
        ident = node['id']; deps = node.get('depends_on', [])
        if not isinstance(deps, list) or len(deps) != len(set(deps)) or set(deps) - seen or 'input' in deps:
            raise ValueError('graph_must_be_acyclic_and_topologically_ordered')
        ancestry = set(deps)
        for d in deps: ancestry.update(ancestors[d])
        ancestors[ident] = ancestry; by_id[ident] = node
        if 'program' in node:
            if 'executor_id' in node or 'arguments' in node: raise ValueError('pure_node_has_no_host_executor')
            validate_program(node['program'])
        else:
            if node.get('executor_id') not in executors: raise ValueError('unknown_host_executor')
            expressions(node.get('arguments', {}), {'input'} | ancestry)
        seen.add(ident)
    expressions(body['output'], seen)
    originals = constraints.get('protected_nodes', {})
    if any(k not in by_id or digest({f:x for f,x in by_id[k].items() if f != 'depends_on'}) != digest({f:x for f,x in v.items() if f != 'depends_on'}) for k, v in originals.items()):
        raise ValueError('original_control_or_external_operation_changed')
    for before, after in constraints.get('protected_precedence', []):
        if before not in ancestors.get(after, set()): raise ValueError('original_external_order_changed')
    external = [n for n in nodes if 'executor_id' in n]
    if set(n['id'] for n in external) != set(originals):
        raise ValueError('new_external_effects_require_separate_host_contract')
    writes = [n for n in external if executors[n['executor_id']].get('effect') == 'write']
    if len(writes) > constraints.get('maximum_writes', 0): raise ValueError('original_write_limit_exceeded')
    for node in writes:
        if not set(constraints.get('required_before_write', [])).issubset(ancestors[node['id']]): raise ValueError('write_without_original_authorization_controls')
        after = constraints.get('required_after_write', {}).get(node['id'], [])
        if not after or any(node['id'] not in ancestors.get(n, set()) for n in after): raise ValueError('write_without_original_reconciliation_and_measurement')
    return {'node_count': len(nodes), 'protected_node_count': len(originals), 'write_count': len(writes)}


def graph_next(body, arguments, constraints, receipts=None):
    """Return one host work request or a completed pure graph. No external I/O.

    Host receipt authenticity must be checked by the caller's existing signed
    source/lease path. A matching digest is a binding, not proof of authenticity.
    """
    validate_graph(body, constraints)
    env = {'input': clone(arguments)}; receipts = receipts or {}; steps = 0
    for node in body['nodes']:
        if 'program' in node:
            data = {'input': arguments, 'dependencies': {k: env[k] for k in node.get('depends_on', [])}}
            result = run_program(node['program'], data, {})
            env[node['id']] = result['output']; steps += result['steps']
        else:
            args = resolve(node.get('arguments', {}), env); binding = digest({'node': node, 'arguments': args, 'constraints': constraints})
            receipt = receipts.get(node['id'])
            if receipt is None:
                return {'state': 'requires_host_executor', 'node_id': node['id'], 'executor_id': node['executor_id'], 'arguments': args,
                        'binding_digest': binding, 'external_execution_authorized': False, 'steps': steps}
            if receipt.get('binding_digest') != binding or receipt.get('state') != 'succeeded' or not receipt.get('source_ref'):
                raise ValueError('original_host_receipt_required')
            env[node['id']] = clone(receipt['output'])
    output = clone(resolve(body['output'], env))
    return {'state': 'completed', 'output': output, 'steps': steps, 'external_execution_authorized': False}


def validate_artifact(artifact, contract):
    artifact, contract = clone(artifact), clone(contract)
    fields = {'schema', 'id', 'kind', 'parent_id', 'scope', 'source_refs', 'constraints_digest', 'hypothesis', 'body'}
    if not isinstance(artifact, dict) or set(artifact) != fields or artifact.get('schema') != SCHEMA or artifact.get('kind') not in ('program', 'graph'):
        raise ValueError('evolution_artifact_contract_invalid')
    if artifact['id'] != digest(artifact['body']): raise ValueError('artifact_content_hash_mismatch')
    if artifact['scope'] != contract.get('scope') or any(not text(artifact['scope'].get(k)) for k in ('tenant_id','brand_id','product_id')):
        raise ValueError('artifact_scope_mismatch')
    context = contract.get('context', {})
    if any(not text(context.get(k)) for k in CONTEXT) or artifact['constraints_digest'] != digest(contract): raise ValueError('original_host_constraints_required')
    if not text(artifact['hypothesis']) or not isinstance(artifact['source_refs'], list) or not artifact['source_refs'] or set(artifact['source_refs']) - set(contract.get('source_refs', [])):
        raise ValueError('original_evidence_sources_required')
    if artifact['kind'] == 'program': validate_program(artifact['body']); checks = {'bounded_dataflow': True}
    else: checks = validate_graph(artifact['body'], contract)
    return {'schema': SCHEMA, 'id': artifact['id'], 'kind': artifact['kind'], 'state': 'candidate', 'checks': checks,
            'constraints_digest': digest(contract), 'external_execution_authorized': False, 'economic_adoption_proven': False}


def make_artifact(kind, body, contract, parent_id, hypothesis, source_refs):
    value = {'schema': SCHEMA, 'id': digest(body), 'kind': kind, 'parent_id': parent_id,
             'scope': contract['scope'], 'constraints_digest': digest(contract), 'source_refs': source_refs,
             'hypothesis': hypothesis, 'body': body}
    validate_artifact(value, contract)
    return value


def evaluate_transfer_trial(plan, rows, now):
    """Prospective held-out matched-condition result. Host authenticates receipts.

    No result supplied by a model is accepted as original evidence by the service.
    This function cannot independently establish that an upstream row is genuine.
    """
    required = ('model', 'initial_tools_digest', 'permission_digest', 'budget_digest', 'outcome_definition')
    if type(plan.get('fixture')) is not bool or not text(plan.get('currency')):raise ValueError('explicit_evidence_class_and_cost_currency_required')
    if plan.get('schema') != SCHEMA or not text(plan.get('assignment_source_ref')) or not isinstance(plan.get('comparison_context'), dict) or any(not text(plan['comparison_context'].get(k)) for k in required):
        raise ValueError('prospective_matched_transfer_contract_required')
    if not plan.get('target_product_id') or plan['target_product_id'] in plan.get('training_product_ids', []) or plan['target_product_id'] == plan.get('source_product_id'):
        raise ValueError('target_must_be_held_out_product')
    if not 0 <= plan.get('registered_at', -1) <= plan.get('start_at', -2) < plan.get('end_at', -3) <= now:
        return {'decision': 'wait', 'reasons': ['prospective_closed_window_required'], 'cross_product_lift_proven': False}
    if plan.get('complete_denominator') is not True: return {'decision':'wait','reasons':['all_assigned_units_including_nonconverters_required'],'cross_product_lift_proven':False}
    units = set(); arms = {'baseline': [], 'candidate': []}
    cost_fields = ('ads','platform_payment','commission','partner_share','delivery','people','software','other')
    for row in rows:
        if row.get('arm') not in arms or not text(row.get('unit_id')) or row['unit_id'] in units or row.get('owner_test') is not False or row.get('external_unit') is not True:
            raise ValueError('independent_external_units_required')
        if row.get('fixture') is not plan.get('fixture') or row.get('target_product_id') != plan['target_product_id'] or row.get('context_digest') != digest(plan['comparison_context']) or row.get('assignment_digest') != digest(plan) or not text(row.get('source_ref')):
            raise ValueError('original_assignment_context_binding_required')
        if not plan['start_at'] <= row.get('occurred_at', -1) < plan['end_at'] or not plan['end_at'] <= row.get('settled_at', -1) <= now or type(row.get('qualified')) is not bool:
            raise ValueError('settled_original_outcome_required')
        if row.get('currency')!=plan['currency'] or row.get('source_class') not in ('product_backend','payment_processor') or not isinstance(row.get('raw_sha256'),str) or len(row['raw_sha256'])!=64 or any(c not in '0123456789abcdef' for c in row['raw_sha256']):raise ValueError('original_source_digest_and_same_cost_currency_required')
        costs = row.get('costs', {})
        if set(costs) != set(cost_fields) or not all(type(v) in (int,float) and math.isfinite(v) and v >= 0 for v in costs.values()): raise ValueError('all_eight_actual_cost_components_required')
        units.add(row['unit_id']); arms[row['arm']].append(row)
    if units != set(plan.get('assigned_unit_ids', [])) or len(plan.get('assigned_unit_ids', [])) != len(units): raise ValueError('complete_prospective_assignment_denominator_required')
    minimum = plan.get('minimum_per_arm', 30)
    if type(minimum) is not int or minimum < 30: raise ValueError('minimum_independent_samples_required')
    if any(len(v) < minimum for v in arms.values()): return {'decision':'wait','reasons':['insufficient_independent_units'],'cross_product_lift_proven':False}
    stats = {}
    for arm, values in arms.items():
        qualified = sum(v['qualified'] for v in values); cost = sum(sum(v['costs'].values()) for v in values)
        stats[arm] = {'units':len(values),'qualified':qualified,'rate':qualified/len(values),'full_cost':cost,'cost_per_qualified':cost/qualified if qualified else None}
        target=plan.get('qualified_target')
        times=sorted(v['occurred_at'] for v in values if v['qualified'])
        stats[arm]['time_to_same_qualified_target']=times[target-1]-plan['start_at'] if type(target) is int and target>0 and len(times)>=target else None
    # Fixed single preregistered test, distribution-free conservative bound.
    lower_lift = stats['candidate']['rate'] - stats['baseline']['rate'] - sum(math.sqrt(math.log(80)/(2*len(v))) for v in arms.values())
    cost_better = stats['candidate']['cost_per_qualified'] is not None and stats['baseline']['cost_per_qualified'] is not None and stats['candidate']['cost_per_qualified'] < stats['baseline']['cost_per_qualified']
    passed = lower_lift > 0 and cost_better
    bt=stats['baseline']['time_to_same_qualified_target'];ct=stats['candidate']['time_to_same_qualified_target']
    faster=bt is not None and ct is not None and ct<bt
    return {'schema':SCHEMA,'decision':'eligible' if passed else 'inconclusive','stats':stats,'qualified_rate_lift_lower':lower_lift,
            'same_conditions':True,'faster_same_target_observed':faster,'faster_and_lower_cost_same_target_verified':passed and faster and plan.get('fixture') is False,'cross_product_lift_proven':passed and plan.get('fixture') is False,
            'controlled_result':plan.get('fixture') is True,'evidence_digest':digest({'plan':plan,'rows':rows}),
            'economic_adoption_proven':False,'generalization_beyond_this_target_proven':False}


def validate_case(artifact, contract, case):
    """Execute a host-owned independent case, replaying ONLY bound originals.

    Replay validates behavior against prior source receipts; it is not a live
    platform action or new economic evidence. Model proposals carry no cases.
    """
    validate_artifact(artifact,contract)
    if artifact['kind']=='program':
        result=run_program(artifact['body'],case['input'],contract)
    else:
        receipts={};originals=case.get('host_replay',{});used=[]
        for _ in range(129):
            result=graph_next(artifact['body'],case['input'],contract,receipts)
            if result['state']=='completed':break
            original=originals.get(result['node_id'])
            if not original or original.get('executor_id')!=result['executor_id'] or original.get('arguments_digest')!=digest(result['arguments']) or not original.get('source_ref') or original.get('source_class') not in ('original_executor','product_backend','payment_processor'):
                raise ValueError('independent_original_host_request_output_binding_required')
            receipts[result['node_id']]={'state':'succeeded','binding_digest':result['binding_digest'],'source_ref':original['source_ref'],'output':original['output']}
            used.append(original['source_ref'])
        else:raise ValueError('bounded_graph_replay_limit')
        if set(originals)!=set(receipts):raise ValueError('original_effect_denominator_changed')
        result={**result,'replayed_original_refs':used,'live_external_actions':0}
    return {'schema':SCHEMA,'artifact_id':artifact['id'],'passed':result.get('output')==case['expected'],
            'input_digest':digest(case['input']),'expected_digest':digest(case['expected']),'output_digest':digest(result.get('output')),
            'steps':result.get('steps'),'host_replay_only':artifact['kind']=='graph' and bool(case.get('host_replay')),
            'live_external_actions':0,'economic_adoption_proven':False}
