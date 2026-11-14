"""Host-bound empirical action estimates, never causal or adoption evidence.

Prior shrinkage stabilizes the point estimate. The interval includes the
distribution-free bounded empirical Hoeffding envelope; its validity assumes
independent units and a stable population, not proven by a receipt. No data
returns unknown rather than a prior-created outcome. All times are epoch sec.
"""
import math
import re
from .commercial_contract import SCHEMA, ACTORS, finite, positive, text, scope, clone

COSTS = ('ads', 'platform_payment', 'commission', 'partner_share', 'delivery', 'people', 'software', 'other')


def _rounded(x):
    if not math.isfinite(x):
        raise ValueError('commercial_estimator_numeric_overflow')
    return float(format(x, '.12f'))


def _bounds(x):
    return isinstance(x, list) and len(x) == 2 and all(finite(v) for v in x) and x[1] > x[0] and finite(x[1] - x[0])


def _estimate(values, bounds, mean, strength, alpha):
    width = bounds[1] - bounds[0]
    normalized = [(x - bounds[0]) / width for x in values]
    n = len(normalized)
    empirical = sum(normalized) / n
    center = (sum(normalized) + strength * mean) / (n + strength)
    radius = math.sqrt(math.log(2 / alpha) / (2 * n))
    lower = min(center, max(0, empirical - radius))
    upper = max(center, min(1, empirical + radius))
    return {'lower': _rounded(bounds[0] + lower * width), 'expected': _rounded(bounds[0] + center * width),
            'upper': _rounded(bounds[0] + upper * width), 'normalized_width': _rounded(upper - lower),
            'evidence_count': n, 'confidence': _rounded(1 - alpha)}


def _transfer_allowed(record, action, data, rights, now):
    if all(record.get(k) == data[k] for k in ('tenant_id', 'brand_id', 'product_id')):
        return record.get('method_version') == action['method_version'] and record.get('context_digest') == data['context_digest']
    for grant in rights:
        if (isinstance(grant, dict) and grant.get('purpose') == 'value_estimation' and grant.get('opt_in') is True
                and grant.get('allow_observation_transfer') is True and grant.get('allow_customer_data') is False
                and grant.get('deidentified_units') is True and text(grant.get('grant_id')) and text(grant.get('evidence_ref'))
                and finite(grant.get('expires_at')) and grant['expires_at'] > now
                and all(grant.get('source_' + k) == record.get(k) and grant.get('target_' + k) == data[k] for k in ('tenant_id', 'brand_id', 'product_id'))
                and record.get('method_version') in grant.get('source_method_versions', []) and grant.get('target_method_version') == action['method_version']
                and grant.get('source_context_digest') == record.get('context_digest') and grant.get('target_context_digest') == data['context_digest']):
            return True
    return False


def _coverage_reason(record, data, observation_ids, trusted, denominators, now):
    pool = denominators.get(record.get('pool_id'))
    if not isinstance(pool, dict):
        return 'prospective_complete_denominator_required'
    fields = ('tenant_id', 'brand_id', 'product_id', 'actor', 'method_version', 'context_digest', 'outcome_definition', 'objective_unit')
    if (any(pool.get(k) != record.get(k) for k in fields) or pool.get('denominator_definition') != data['denominator_definition']
            or pool.get('coverage_complete') is not True or pool.get('source_class') != 'product_backend'
            or not text(pool.get('source_ref')) or not isinstance(pool.get('raw_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', pool['raw_sha256'])):
        return 'original_denominator_binding_required'
    if (not all(finite(pool.get(k)) for k in ('registered_at', 'window_start', 'window_end', 'observed_at'))
            or not pool['registered_at'] <= pool['window_start'] < pool['window_end'] <= pool['observed_at'] <= now
            or not finite(record.get('occurred_at')) or not pool['window_start'] <= record['occurred_at'] < pool['window_end']):
        return 'denominator_not_prospectively_registered_or_closed'
    units = pool.get('unit_ids')
    if not isinstance(units, list) or not units or any(not text(x) for x in units) or len(set(units)) != len(units):
        return 'original_eligible_unit_pool_required'
    rows = [trusted[ident] for ident in {x for x in observation_ids if isinstance(x, str)} if isinstance(trusted.get(ident), dict)
            and trusted[ident].get('pool_id') == record.get('pool_id') and all(trusted[ident].get(k) == pool.get(k) for k in fields)]
    if len(rows) != len(units) or {r.get('unit_id') for r in rows} != set(units):
        return 'eligible_denominator_coverage_incomplete'
    return None


def estimateCommercialActionValues(data, now, trusted_observations=None, transfer_rights=None, trusted_denominators=None):
    scope(data)
    trusted_observations, transfer_rights, trusted_denominators = trusted_observations or {}, transfer_rights or [], trusted_denominators or {}
    if (not text(data.get('product_id')) or not text(data.get('outcome_definition')) or not text(data.get('context_digest'))
            or data.get('objective_unit') not in ('qualified_users', 'currency') or not finite(now)
            or not _bounds(data.get('value_bounds')) or not isinstance(data.get('observation_ids'), list)
            or len(data['observation_ids']) > 10000 or not isinstance(data.get('actions'), list) or len(data['actions']) > 100):
        raise ValueError('commercial_estimator_contract_invalid')
    if data.get('estimand') not in ('outcomes_per_eligible_opportunity', 'contribution_per_paid_customer') or not text(data.get('denominator_definition')):
        raise ValueError('commercial_estimand_and_denominator_required')
    if data['estimand'] == 'contribution_per_paid_customer' and data['objective_unit'] != 'currency':
        raise ValueError('paid_customer_contribution_requires_currency')
    if data['objective_unit'] == 'qualified_users' and data['value_bounds'] != [0, 1]:
        raise ValueError('qualified_user_values_must_be_binary_per_independent_unit')
    if data.get('cost_bounds') is not None and (not _bounds(data['cost_bounds']) or data['cost_bounds'][0] < 0):
        raise ValueError('commercial_cost_bounds_invalid')
    confidence = data.get('confidence') if data.get('confidence') is not None else .95
    prior = data.get('prior') if data.get('prior') is not None else {'mean': .5, 'strength': 2}
    if (not finite(confidence) or not .9 <= confidence < 1 or not isinstance(prior, dict)
            or not finite(prior.get('mean')) or not 0 <= prior['mean'] <= 1 or not finite(prior.get('strength')) or not 0 < prior['strength'] <= 100):
        raise ValueError('commercial_estimator_prior_invalid')
    maximum_age = data.get('maximum_age_seconds') if data.get('maximum_age_seconds') is not None else 30 * 86400
    if not positive(maximum_age) or maximum_age == 0:
        raise ValueError('commercial_estimator_age_invalid')
    action_ids, results = set(), []
    for raw in data['actions']:
        action = clone(raw)
        if not isinstance(action, dict) or not text(action.get('id')) or action['id'] in action_ids:
            raise ValueError('commercial_estimator_action_id_invalid')
        action_ids.add(action['id'])
        reasons, accepted, accepted_rows, seen_units, values, costs, cost_unknown = [], [], [], set(), [], [], False
        valid_action = action.get('actor') in ACTORS and text(action.get('method_version')) and text(action.get('channel')) and positive(action.get('expected_units')) and action['expected_units'] > 0
        if not valid_action:
            reasons.append({'id': None, 'reason': 'known_actor_method_channel_and_expected_units_required'})
        if valid_action:
            for ident in data['observation_ids']:
                record = trusted_observations.get(ident) if isinstance(ident, str) else None
                reason = None
                if not isinstance(record, dict):
                    reason = 'original_host_observation_required'
                elif not _transfer_allowed(record, action, data, transfer_rights, now):
                    reason = 'scope_method_context_or_right_mismatch'
                elif (record.get('actor') != action['actor'] or record.get('actor_binding') not in ('authenticated_outcome_context', 'authenticated_purchase_context')
                      or record.get('channel') != action['channel'] or record.get('objective_unit') != data['objective_unit'] or record.get('outcome_definition') != data['outcome_definition']):
                    reason = 'actor_channel_or_success_definition_mismatch'
                elif record.get('source_class') not in ('product_backend', 'payment_processor') or not text(record.get('source_ref')) or not isinstance(record.get('raw_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', record['raw_sha256']):
                    reason = 'original_source_binding_required'
                elif (record.get('complete') is not True or record.get('external_unit') is not True or record.get('owner_test') is not False
                      or not finite(record.get('occurred_at')) or not finite(record.get('observed_at')) or not finite(record.get('conversion_window_end'))
                      or not now - maximum_age <= record['occurred_at'] <= record['conversion_window_end'] <= record['observed_at'] <= now):
                    reason = 'incomplete_stale_or_open_conversion_window'
                elif not text(record.get('unit_id')) or (record.get('tenant_id'), record.get('brand_id'), record.get('unit_id')) in seen_units:
                    reason = 'duplicate_or_missing_independent_unit'
                if not reason and data['estimand'] == 'outcomes_per_eligible_opportunity':
                    reason = _coverage_reason(record, data, data['observation_ids'], trusted_observations, trusted_denominators, now)
                money = [record.get(k) for k in ('receipts', 'refunds', 'reserves')] if isinstance(record, dict) else []
                cost_values = [record.get('costs', {}).get(k) for k in COSTS] if isinstance(record, dict) and isinstance(record.get('costs'), dict) else [None]
                complete_cost = len(cost_values) == len(COSTS) and all(positive(v) for v in cost_values)
                if not reason and data['objective_unit'] == 'currency':
                    if record.get('currency') != data.get('currency') or not text(data.get('currency')):
                        reason = 'currency_mismatch'
                    elif (not all(positive(v) for v in money) or not complete_cost or not finite(record.get('refund_window_end'))
                          or not record['conversion_window_end'] <= record['refund_window_end'] <= record['observed_at']):
                        reason = 'unknown_full_cost_money_or_open_refund_window'
                value = sum([money[0], -money[1], -money[2]]) if not reason and data['objective_unit'] == 'currency' else record.get('value') if isinstance(record, dict) else None
                if not reason and data['estimand'] == 'contribution_per_paid_customer':
                    if money[0] <= 0:
                        reason = 'original_paid_receipt_required'
                    else:
                        value -= sum(cost_values)
                if not reason and (not finite(value) or not data['value_bounds'][0] <= value <= data['value_bounds'][1] or data['objective_unit'] == 'qualified_users' and value not in (0, 1)):
                    reason = 'value_outside_registered_bounds'
                if reason:
                    reasons.append({'id': ident, 'reason': reason})
                    continue
                seen_units.add((record['tenant_id'], record['brand_id'], record['unit_id']))
                accepted_rows.append({'id': ident, 'unit_id': record['unit_id'], 'pool_id': record.get('pool_id'), 'value': value,
                                      'cost': sum(cost_values) if complete_cost else None})
        if data['estimand'] == 'outcomes_per_eligible_opportunity':
            for pool_id in {row['pool_id'] for row in accepted_rows}:
                pool_rows = [r for r in accepted_rows if r['pool_id'] == pool_id]
                pool = trusted_denominators.get(pool_id, {})
                if {r['unit_id'] for r in pool_rows} != set(pool.get('unit_ids', [])):
                    reasons.extend({'id': r['id'], 'reason': 'incomplete_eligible_pool_after_validation'} for r in pool_rows)
                    accepted_rows = [r for r in accepted_rows if r['pool_id'] != pool_id]
        for row in accepted_rows:
            accepted.append(row['id']); values.append(row['value'])
            if row['cost'] is not None and data.get('cost_bounds') is not None and data['cost_bounds'][0] <= row['cost'] <= data['cost_bounds'][1]:
                costs.append(row['cost'])
            else:
                cost_unknown = True
        forecast, cost_forecast = None, None
        if values:
            alpha = (1 - confidence) / 2
            estimate = _estimate(values, data['value_bounds'], prior['mean'], prior['strength'], alpha)
            units = action['expected_units']
            if data['estimand'] == 'outcomes_per_eligible_opportunity':
                forecast = {k: _rounded(estimate[k] * units) for k in ('lower', 'expected', 'upper')}
                forecast.update(confidence=confidence, evidence_count=len(values))
            info = _rounded(estimate['normalized_width'] / (len(values) + 1))
            if not cost_unknown and len(costs) == len(values):
                cost_forecast = _estimate(costs, data['cost_bounds'], prior['mean'], prior['strength'], alpha)
                cost_forecast = {**{k: _rounded(cost_forecast[k] * units) for k in ('lower', 'expected', 'upper')}, 'evidence_count': len(costs)}
        else:
            estimate = None
            info = 1 if valid_action else 0
        results.append({**action, 'forecast': forecast, 'full_cost_upper': cost_forecast['upper'] if cost_forecast and forecast else None,
                        'information_gain': info, 'prediction_verified': False,
                        'empirical_estimate': {'status': 'empirical_estimate' if values else 'unknown', 'independent_units': len(values),
                         'accepted_observation_ids': accepted, 'rejected_observations': reasons, 'per_unit': estimate,
                         'cost_forecast': cost_forecast, 'cost_status': 'statistical_estimate' if cost_forecast else 'unknown',
                         'cost_bound_is_spend_authority': False, 'prior': clone(prior), 'outcome_unit': data['objective_unit'],
                         'estimand': data['estimand'], 'denominator_definition': data['denominator_definition'],
                         'conditional_per_paid_customer': estimate if data['estimand'] == 'contribution_per_paid_customer' else None,
                         'method': 'prior_shrunk_mean_with_empirical_hoeffding_envelope',
                         'information_priority_method': 'normalized_interval_width_per_next_independent_unit',
                         'independence_and_stationarity_assumed': True, 'causal_effect_proven': False, 'forward_accuracy_verified': False}})
    return {'schema': SCHEMA, 'estimator_version': 'empirical-action-v1', 'tenant_id': data['tenant_id'], 'brand_id': data['brand_id'],
            'product_id': data['product_id'], 'objective_unit': data['objective_unit'], 'actions': results,
            'calibration_status': 'unvalidated_forward_prediction', 'measured_lift': False, 'execution_authorized': False, 'generated_at': now}


estimate_commercial_action_values = estimateCommercialActionValues
