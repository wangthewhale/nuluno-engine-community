"""Fixed-horizon, brand-local evidence gates for reversible method adoption."""
import math
import random
from .files import digest


def evaluation_policy(body):
    policy={'minimum_units':30,'confidence':0.95,'planned_comparisons':1,'resamples':2000,'refund_ratio_tolerance':0.0,**body.get('evaluation',{})}
    if type(policy['minimum_units']) is not int or not 30<=policy['minimum_units']<=5000:raise ValueError('method adoption needs at least 30 independent units per arm; this is a guard, not a universal power calculation')
    if type(policy['confidence']) not in (int,float) or not math.isfinite(policy['confidence']) or not .9<=policy['confidence']<=.999:raise ValueError('Invalid confidence')
    if type(policy['planned_comparisons']) is not int or not 1<=policy['planned_comparisons']<=20:raise ValueError('Invalid planned comparison count')
    if type(policy['resamples']) is not int or not 2000<=policy['resamples']<=10000:raise ValueError('Invalid bootstrap resample count')
    if type(policy['refund_ratio_tolerance']) not in (int,float) or not math.isfinite(policy['refund_ratio_tolerance']) or policy['refund_ratio_tolerance']<0:raise ValueError('Invalid refund guardrail')
    return policy


def uncertainty(plan, observations, values, mode):
    from .contracts import _proof
    policy=plan['evaluation'];reasons=[];arms={};identities=[]
    context=digest(plan['comparison_context'])
    for arm in ('baseline','candidate'):
        sample=observations.get(arm,{})
        if sample.get('context_digest')!=context:reasons.append(arm+':comparison_context_mismatch')
        units=sample.get('customer_units',[]);count=sample.get('customers')
        if not isinstance(units,list) or type(count) is not int or len(units)!=count or not max(plan['minimum_sample'],policy['minimum_units'])<=len(units)<=5000:
            reasons.append(arm+':insufficient_independent_units');continue
        ids=[];nets=[]
        for row in units:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'] or not _proof(row.get('source_ref'),mode) or type(row.get('net')) not in (int,float) or not math.isfinite(row['net']):
                reasons.append(arm+':invalid_unit_evidence');continue
            ids.append(row['id']);nets.append(row['net'])
        if len(ids)!=len(set(ids)):reasons.append(arm+':duplicate_customer_unit')
        identities.append(set(ids))
        if arm in values and len(nets)==count and not math.isclose(sum(nets),values[arm]*count,abs_tol=1e-6,rel_tol=1e-8):reasons.append(arm+':unit_net_does_not_reconcile')
        arms[arm]=nets
    if len(identities)==2 and identities[0]&identities[1]:reasons.append('customer_units_overlap_between_arms')
    if reasons:return {'reasons':sorted(set(reasons)),'interval':None}
    rng=random.Random(int(digest({'plan':plan,'observations':observations})[:16],16))
    a,b=arms['baseline'],arms['candidate'];na,nb=len(a),len(b)
    draws=sorted(sum(rng.choices(b,k=nb))/nb-sum(rng.choices(a,k=na))/na for _ in range(policy['resamples']))
    alpha=(1-policy['confidence'])/policy['planned_comparisons'];tail=alpha/2
    def quantile(q):
        position=q*(len(draws)-1);index=int(position);weight=position-index
        return draws[index]*(1-weight)+draws[min(index+1,len(draws)-1)]*weight
    return {'reasons':[],'interval':{'lower':quantile(tail),'upper':quantile(1-tail),'method':'independent percentile bootstrap','confidence':1-alpha,'planned_comparisons':policy['planned_comparisons'],'resamples':len(draws),'independence_assumed_not_proven':True}}
