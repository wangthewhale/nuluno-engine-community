"""JSON contract shared with Nuluno's commercial-learning.ts.

Pure planning and validation only: host registries/rights must come from trusted
configuration. No offer, forecast or declarative method grants execution.
"""
import copy
import hashlib
import json
import math
import re
from decimal import Decimal
from urllib.parse import urlsplit

SCHEMA = 'nuluno-commercial-v1'
FACT_KINDS = {'product', 'benefit', 'price', 'delivery', 'demo', 'limitation'}
SOURCES = {'public_source', 'owner_verified', 'product_backend', 'payment_processor', 'platform_receipt'}
ACTORS = {'human', 'buyer_ai'}
METHOD_FIELDS = {'audience', 'angle', 'channel', 'content_format', 'followup', 'query', 'skill_method', 'graph_method'}


def finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def positive(x):
    return finite(x) and x >= 0


def text(x):
    return isinstance(x, str) and bool(x.strip())


def canonical_contract(value):
    """ECMAScript-compatible canonical JSON for ordinary JSON double values.

    Whole floats and scientific exponent formatting differ in json.dumps and
    JSON.stringify; normalize them so cross-host offer hashes remain usable.
    """
    if value is None:
        return 'null'
    if type(value) is bool:
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if finite(value):
        number = float(value)
        if number == 0:
            return '0'
        rendered = repr(number).lower()
        if 1e-6 <= abs(number) < 1e21:
            if number.is_integer():
                return str(int(number))
            return format(Decimal(rendered), 'f').rstrip('0').rstrip('.')
        mantissa, sep, exponent = rendered.partition('e')
        mantissa = mantissa.removesuffix('.0')
        if sep:
            power = int(exponent)
            return mantissa + 'e' + ('+' if power >= 0 else '') + str(power)
        return mantissa
    if isinstance(value, list):
        return '[' + ','.join(canonical_contract(x) for x in value) + ']'
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return '{' + ','.join(canonical_contract(k) + ':' + canonical_contract(value[k]) for k in sorted(value)) + '}'
    raise ValueError('Finite JSON values required')


def contract_digest(value):
    return hashlib.sha256(canonical_contract(value).encode('utf-8')).hexdigest()


def clone(value):
    canonical_contract(value)
    return copy.deepcopy(value)


def scope(value):
    if not isinstance(value, dict) or not text(value.get('tenant_id')) or not text(value.get('brand_id')):
        raise ValueError('commercial_scope_required')


def public_url(value):
    if not text(value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname) and not url.username and not url.password and not any(x.isspace() for x in value)
    except ValueError:
        return False


def source_reason(source, now):
    if not isinstance(source, dict) or not text(source.get('reference')) or source.get('source_class') not in SOURCES:
        return 'source_required'
    if source['source_class'] == 'public_source' and not public_url(source['reference']):
        return 'public_source_https_required'
    if not finite(source.get('verified_at')) or source['verified_at'] > now:
        return 'source_timestamp_invalid'
    if 'expires_at' in source and (not finite(source['expires_at']) or source['expires_at'] <= now):
        return 'source_expired'
    return None


def host_bound(fact, product, trusted, now):
    s = fact['source']
    t = trusted.get(s['reference'])
    return bool(isinstance(t, dict) and t.get('tenant_id') == product['tenant_id'] and t.get('brand_id') == product['brand_id']
                and t.get('product_id', product['product_id']) == product['product_id']
                and t.get('reference', s['reference']) == s['reference'] and t.get('source_class') == s['source_class']
                and finite(t.get('verified_at')) and t['verified_at'] == s['verified_at'] and t['verified_at'] <= now
                and ('expires_at' not in t or finite(t['expires_at']) and t['expires_at'] > now)
                and bool(re.fullmatch('[a-f0-9]{64}', str(s.get('content_sha256', ''))))
                and (t.get('payload_sha256') or t.get('content_sha256')) == s['content_sha256']
                and isinstance(t.get('facts'), dict) and t['facts'].get(fact['id']) == contract_digest(fact['value']))


def compileCommercialOffer(product, now, trusted_sources=None):
    scope(product)
    if not text(product.get('product_id')) or not finite(now) or not isinstance(product.get('facts'), list):
        raise ValueError('commercial_product_contract_invalid')
    facts, excluded, ids = [], [], set()
    for raw in product['facts']:
        f = raw if isinstance(raw, dict) else {}
        reason = ('fact_id_invalid_or_duplicate' if not text(f.get('id')) or f['id'] in ids else
                  'fact_kind_invalid' if f.get('kind') not in FACT_KINDS else
                  'fact_value_missing' if f.get('value') is None else source_reason(f.get('source'), now))
        value = f.get('value')
        if not reason and f['kind'] == 'price' and (not isinstance(value, dict) or not positive(value.get('amount')) or not re.fullmatch('[A-Z]{3}', str(value.get('currency', '')))):
            reason = 'structured_price_required'
        if not reason and f['kind'] == 'demo' and not public_url(value if isinstance(value, str) else value.get('url') if isinstance(value, dict) else None):
            reason = 'demo_https_required'
        if reason:
            excluded.append({'id': f.get('id'), 'reason': reason})
            continue
        ids.add(f['id'])
        facts.append({'id': f['id'], 'kind': f['kind'], 'value': clone(value), 'source': clone(f['source']),
                      'evidence_status': 'host_bound' if host_bound(f, product, trusted_sources or {}, now) else 'source_linked'})
    propositions = []
    for p in product.get('value_propositions', []):
        if not isinstance(p, dict) or not text(p.get('text')) or not isinstance(p.get('fact_ids'), list) or not p['fact_ids'] or any(x not in ids for x in p['fact_ids']):
            excluded.append({'id': None, 'reason': 'value_proposition_fact_binding_missing'})
            continue
        bound = all(next(f for f in facts if f['id'] == ident)['evidence_status'] == 'host_bound' for ident in p['fact_ids'])
        propositions.append({'text': p['text'], 'fact_ids': p['fact_ids'][:], 'evidence_status': 'hypothesis',
                             'fact_binding_status': 'host_bound' if bound else 'source_linked', 'causal_effect_proven': False})
    missing = [kind for kind in ('product', 'price', 'delivery', 'demo') if not any(f['kind'] == kind and f['evidence_status'] == 'host_bound' for f in facts)]
    machine = {'schema': SCHEMA, 'tenant_id': product['tenant_id'], 'brand_id': product['brand_id'], 'product_id': product['product_id'],
               'facts': facts, 'value_propositions': propositions, 'missing_verified_fields': missing, 'offer_complete': not missing,
               'purchase_ready': False, 'checkout_verified': False, 'independently_verified': False, 'execution_authorized': False, 'generated_at': now}
    summary = '\n'.join(p['text'] for p in propositions) or '\n'.join(f['value'] if isinstance(f['value'], str) else canonical_contract(f['value']) for f in facts if f['kind'] == 'product')
    return {**machine, 'human': {'summary': summary, 'fact_ids': [f['id'] for f in facts], 'sources': list(dict.fromkeys(f['source']['reference'] for f in facts))},
            'buyer_ai': clone(machine), 'excluded': excluded, 'digest': contract_digest(machine)}


def rankCommercialActions(data, now):
    scope(data)
    budget = data.get('budget')
    if not isinstance(data.get('actions'), list) or not isinstance(budget, dict) or not re.fullmatch('[A-Z]{3}', str(budget.get('currency', ''))) or not finite(now):
        raise ValueError('commercial_action_contract_invalid')
    if budget.get('remaining') is not None and not positive(budget['remaining']):
        raise ValueError('commercial_budget_invalid')
    ranked, rejected, ids = [], [], set()
    for raw in data['actions']:
        a = raw if isinstance(raw, dict) else {}
        forecast, reasons = a.get('forecast'), []
        cash = a.get('incremental_cash_upper')
        if cash is None:
            cash = a.get('cost_upper')
        full_cost = a.get('full_cost_upper')
        if not text(a.get('id')) or a['id'] in ids:
            reasons.append('action_id_invalid_or_duplicate')
        else:
            ids.add(a['id'])
        if a.get('actor') not in ACTORS and not (a.get('actor') == 'unknown' and forecast is None):
            reasons.append('actor_required')
        if not text(a.get('kind')) or not text(a.get('channel')) or not isinstance(a.get('source_refs'), list) or not a['source_refs'] or not all(text(x) for x in a['source_refs']):
            reasons.append('action_evidence_required')
        if not positive(cash):
            reasons.append('incremental_cash_upper_unknown')
        if full_cost is not None and not positive(full_cost):
            reasons.append('full_cost_upper_invalid')
        if a.get('currency') != budget['currency']:
            reasons.append('currency_mismatch')
        if finite(cash) and cash > 0 and budget.get('remaining') is None:
            reasons.append('budget_unknown')
        if finite(budget.get('remaining')) and finite(cash) and cash > budget['remaining']:
            reasons.append('budget_exceeded')
        if forecast is not None:
            if (not isinstance(forecast, dict) or not all(finite(forecast.get(k)) for k in ('lower', 'expected', 'upper'))
                    or forecast['lower'] > forecast['expected'] or forecast['expected'] > forecast['upper']
                    or not finite(forecast.get('confidence')) or not 0 < forecast['confidence'] < 1
                    or type(forecast.get('evidence_count')) is not int or forecast['evidence_count'] < 0):
                reasons.append('forecast_uncertainty_required')
        if not positive(a.get('information_gain')):
            reasons.append('information_gain_invalid')
        if reasons:
            rejected.append({'id': a.get('id'), 'reasons': reasons})
            continue
        monetary = data.get('objective_unit') == 'currency' and full_cost is not None and forecast is not None
        lower = forecast['lower'] - full_cost if monetary else None
        expected = forecast['expected'] - full_cost if monetary else None
        ranked.append({**clone(a), 'incremental_cash_upper': cash, 'full_cost_upper': full_cost,
                       'objective_unit': data.get('objective_unit', 'unspecified'), 'estimated_net_lower': lower,
                       'estimated_net_expected': expected, 'estimated_outcome_lower': forecast['lower'] if forecast else None,
                       'score': lower, 'forecast_status': 'hypothesis' if forecast else 'unknown',
                       'selection_reason': 'collect_missing_outcome_evidence' if not forecast else 'compare_unproven_action',
                       'prediction_verified': False, 'execution_authorized': False})
    value_first = data.get('selection_policy') == 'conservative_value'
    from functools import cmp_to_key
    def compare(a, b):
        if value_first and finite(a['score']) and finite(b['score']) and a['score'] != b['score']:
            return -1 if a['score'] > b['score'] else 1
        for a_value, b_value in ((-a['information_gain'], -b['information_gain']), (a['incremental_cash_upper'], b['incremental_cash_upper']), (a['id'], b['id'])):
            if a_value != b_value:
                return -1 if a_value < b_value else 1
        return 0
    ranked.sort(key=cmp_to_key(compare))
    return {'schema': SCHEMA, 'tenant_id': data['tenant_id'], 'brand_id': data['brand_id'], 'ranked': ranked, 'rejected': rejected,
            'selected_action_id': ranked[0]['id'] if ranked else None,
            'selection_policy': 'conservative_value_then_information' if value_first else 'information_then_incremental_cash',
            'measured_lift': False, 'generated_at': now}


def designCommercialExperiment(data, now):
    scope(data)
    ranking = rankCommercialActions(data, now)
    if not ranking['ranked']:
        return {'schema': SCHEMA, 'status': 'needs_data', 'ranking': ranking, 'execution_authorized': False}
    selected, p = ranking['ranked'][0], data.get('experiment')
    if (not isinstance(p, dict) or not text(p.get('hypothesis')) or not text(p.get('metric')) or type(p.get('minimum_sample')) is not int or p['minimum_sample'] < 1
            or not finite(p.get('start_at')) or p['start_at'] < now or not finite(p.get('end_at')) or p['end_at'] <= p['start_at']
            or not positive(p.get('minimum_improvement')) or not isinstance(p.get('metric_bounds'), list) or len(p['metric_bounds']) != 2
            or not all(finite(x) for x in p['metric_bounds']) or p['metric_bounds'][1] <= p['metric_bounds'][0]
            or not text(p.get('assignment_unit')) or not text(p.get('baseline_method_id')) or not text(p.get('candidate_method_id')) or p['baseline_method_id'] == p['candidate_method_id']):
        raise ValueError('commercial_preregistration_required')
    c = p.get('comparison_context')
    if not isinstance(c, dict) or not all(text(c.get(k)) for k in ('model', 'initial_tools_digest', 'permission_digest', 'budget_digest', 'outcome_definition')):
        raise ValueError('commercial_comparison_context_required')
    plan = {**clone(p), 'schema': SCHEMA, 'tenant_id': data['tenant_id'], 'brand_id': data['brand_id'], 'actor': selected['actor'], 'action_id': selected['id'],
            'incremental_cash_upper': selected['incremental_cash_upper'], 'full_cost_upper': selected['full_cost_upper'], 'currency': selected['currency'],
            'prospective': True, 'execution_authorized': False, 'causal_effect_proven': False}
    return {'schema': SCHEMA, 'status': 'proposed', 'id': contract_digest(plan), 'plan': plan, 'ranking': ranking, 'execution_authorized': False}


def validateCommercialMethod(method, constraints):
    scope(method)
    changes = method.get('changes')
    if (not text(method.get('parent_id')) or not text(method.get('reason')) or not isinstance(method.get('source_refs'), list) or not method['source_refs']
            or not all(text(x) for x in method['source_refs']) or not isinstance(changes, dict) or not changes or set(changes) - METHOD_FIELDS):
        raise ValueError('commercial_method_contract_invalid')
    if method['tenant_id'] != constraints.get('tenant_id') or method['brand_id'] != constraints.get('brand_id'):
        raise ValueError('commercial_method_scope_mismatch')
    if any(not text(value) for key, value in changes.items() if key not in ('skill_method', 'graph_method')):
        raise ValueError('commercial_method_text_required')
    allowed = set(constraints.get('allowed_skill_ids', []))
    if 'skill_method' in changes:
        skill = changes['skill_method']
        if not isinstance(skill, dict) or skill.get('skill_id') not in allowed or not text(skill.get('instructions')) or set(skill) - {'skill_id', 'instructions'}:
            raise ValueError('commercial_skill_not_available')
    if changes.get('channel') and changes['channel'] not in constraints.get('allowed_channels', []):
        raise ValueError('commercial_channel_not_available')
    if 'graph_method' in changes:
        graph = changes['graph_method']
        mandatory = constraints.get('mandatory_skill_ids', [])
        if (not isinstance(graph, dict) or not isinstance(graph.get('skill_ids'), list) or not graph['skill_ids'] or any(x not in allowed for x in graph['skill_ids'])
                or not text(graph.get('original_contract_digest')) or graph['original_contract_digest'] != constraints.get('original_contract_digest')
                or not isinstance(graph.get('mandatory_skill_ids'), list) or graph['mandatory_skill_ids'] != mandatory
                or any(x not in graph['skill_ids'] for x in mandatory) or set(graph) - {'skill_ids', 'mandatory_skill_ids', 'original_contract_digest'}):
            raise ValueError('commercial_graph_contract_changed')
        if [x for x in graph['skill_ids'] if x in mandatory] != mandatory:
            raise ValueError('commercial_mandatory_order_changed')
    return {**clone(method), 'id': contract_digest(method), 'state': 'candidate', 'permissions_unchanged': True,
            'validation_scope': 'declarative_contract_only', 'execution_authorized': False, 'commercial_improvement_proven': False}


def decideCommercialTransfer(method, rights, target, now):
    scope(method)
    scope(target)
    reasons = []
    if method.get('state') not in ('adopted', 'validated') or method.get('commercial_improvement_proven') is not True or not text(method.get('evidence_digest')):
        reasons.append('validated_method_evidence_required')
    if method['tenant_id'] == target['tenant_id'] and method['brand_id'] == target['brand_id']:
        return {'allowed': not reasons, 'scope': 'same_brand', 'reasons': reasons, 'applicability_verified': False, 'execution_authorized': False}
    if (not isinstance(rights, dict) or rights.get('opt_in') is not True or rights.get('source_tenant_id') != method['tenant_id']
            or rights.get('source_brand_id') != method['brand_id'] or rights.get('target_tenant_id') != target['tenant_id'] or rights.get('target_brand_id') != target['brand_id']
            or not isinstance(rights.get('method_ids'), list) or method.get('id') not in rights['method_ids'] or not text(rights.get('grant_id'))
            or not text(rights.get('evidence_ref')) or not finite(rights.get('expires_at')) or rights['expires_at'] <= now
            or rights.get('allow_strategy_transfer') is not True or rights.get('allow_customer_data') is not False):
        reasons.append('explicit_scoped_transfer_right_required')
    applicability = method.get('applicability', {})
    if (not isinstance(applicability, dict) or not isinstance(applicability.get('product_categories'), list) or target.get('product_category') not in applicability['product_categories']
            or not isinstance(applicability.get('actors'), list) or target.get('actor') not in applicability['actors']):
        reasons.append('applicability_mismatch')
    return {'allowed': not reasons, 'scope': 'cross_brand_candidate', 'reasons': reasons, 'target_status': 'requires_independent_prospective_trial',
            'applicability_verified': False, 'private_customer_data_transferred': False, 'execution_authorized': False}


compile_commercial_offer = compileCommercialOffer
rank_commercial_actions = rankCommercialActions
design_commercial_experiment = designCommercialExperiment
validate_commercial_method = validateCommercialMethod
decide_commercial_transfer = decideCommercialTransfer


def estimateCommercialActionValues(data, now, trusted_observations=None, transfer_rights=None, trusted_denominators=None):
    from .action_estimator import estimateCommercialActionValues as estimate
    return estimate(data, now, trusted_observations, transfer_rights, trusted_denominators)


estimate_commercial_action_values = estimateCommercialActionValues
