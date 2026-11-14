"""Durable artifact lifecycle shared by HTTP/MCP/Python. Original Store owns state.

Technical adoption means independently verified execution improvement only.
Economic adoption requires the separate settled commercial outcome gates.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from .artifact_kernel import SCHEMA, make_artifact, validate_artifact, digest
from .files import canonical, redact
from .errors import WorkflowError


def isolated(request):
    """Fixed trusted runner; proposed JSON is stdin, never executable source.

    -I/-S removes user site/startup hooks; env contains no connector/model secrets.
    There is no claim of sandboxing arbitrary Python, which is never accepted.
    """
    runtime = Path(__file__).resolve().parents[1]
    code = 'import sys;sys.path.insert(0,sys.argv[1]);from growth_loop.evolution_bridge import main;main()'
    result = subprocess.run([sys.executable,'-I','-S','-c',code,str(runtime)],input=canonical(request).encode(),
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5,
                            env={'PATH':os.defpath,'PYTHONHASHSEED':'0'},cwd=str(runtime))
    if result.returncode != 0: raise ValueError('isolated_runner_failed_or_resource_limit')
    if len(result.stdout)>1024*1024: raise ValueError('isolated_runner_output_limit')
    value=json.loads(result.stdout)
    if 'error' in value: raise ValueError(value['error']['message'])
    return value['result']


class ArtifactEvolution:
    def __init__(self,store,config):
        self.store,self.config=store,config
        self.settings=config.get('artifact_evolution',{})

    def contract(self,run_id):
        contract=self.settings.get('contracts',{}).get(run_id)
        if not isinstance(contract,dict): raise WorkflowError('Original host artifact contract is not connected.','waiting_capability')
        profile=self.store.config(run_id)
        if contract.get('scope',{}).get('brand_id')!=profile['brand']['url'] or contract['scope'].get('product_id')!=(profile.get('product_id') or profile['brand']['url']):
            raise WorkflowError('Artifact contract does not belong to this product.','scope_mismatch')
        return contract

    def allowed(self,run_id):
        if self.config.get('allow_evolution') is not True or self.settings.get('enabled') is not True:
            raise WorkflowError('Artifact evolution is not enabled by the original host.','scope_mismatch')
        if (self.store.root/'.transfer.lock').exists() or self.store.artifact(run_id,0,'graph.stopped'):
            raise WorkflowError('Original workspace is frozen or stopped.','scope_mismatch')
        return self.contract(run_id)

    def get(self,run_id,ident):
        value=self.store.artifact(run_id,0,'evolution.artifact.'+ident)
        if value:return value
        baseline=self.settings.get('baselines',{}).get(run_id,{}).get(ident)
        if baseline:
            validate_artifact(baseline,self.contract(run_id))
            return {'artifact':baseline,'state':'original','created_at':0}
        raise ValueError('unknown_original_artifact')

    def head(self,run_id,kind):
        return self.store.artifact(run_id,0,'evolution.artifact_head.'+kind) or {'id':self.settings.get('original_heads',{}).get(run_id,{}).get(kind),'previous':None}

    def propose(self,run_id,body):
        contract=self.allowed(run_id)
        if set(body)-{'kind','body','parent_id','hypothesis','source_refs'}:raise ValueError('models_cannot_submit_validation_or_adoption')
        parent=self.head(run_id,body['kind'])['id']
        if body.get('parent_id',parent)!=parent:raise ValueError('candidate_must_use_current_original_parent')
        artifact=make_artifact(body['kind'],body['body'],contract,parent,body['hypothesis'],body['source_refs'])
        if parent==artifact['id']:raise ValueError('no_behavioral_change')
        old=self.store.artifact(run_id,0,'evolution.artifact.'+artifact['id'])
        if old and old['artifact']!=artifact:raise ValueError('same_content_has_different_original_context')
        if not old:self.store.artifact(run_id,0,'evolution.artifact.'+artifact['id'],{'artifact':artifact,'state':'candidate','created_at':self.store.clock()})
        return {'schema':SCHEMA,'artifact':artifact,'state':(old or {}).get('state','candidate'),'economic_adoption_proven':False}

    def generate(self,run_id,body):
        from .artifact_policy import PROPOSAL_FIELDS,PROPOSAL_INSTRUCTIONS
        from .model_adapter import ProposalModel
        contract=self.allowed(run_id)
        if set(body)-{'kind','source_refs'} or body.get('kind') not in ('program','graph'):raise ValueError('bounded_generation_request_required')
        sources=self.settings.get('sources',{})
        refs=body.get('source_refs',[])
        if not refs or set(refs)-set(contract['source_refs']) or any(ref not in sources for ref in refs):raise ValueError('host_owned_original_generation_sources_required')
        if any(sources[ref].get('model_use_allowed') is not True and sources[ref].get('public') is not True for ref in refs):raise ValueError('original_source_model_use_permission_required')
        parent=self.head(run_id,body['kind'])['id']
        context={'kind':body['kind'],'contract':contract,'parent':self.get(run_id,parent)['artifact'] if parent else None,
                 'sources':{ref:sources[ref] for ref in refs},'independent_cases':'withheld_by_host'}
        key='evolution.model.artifact.'+digest(context)
        proposal=ProposalModel(self.store,self.settings.get('model',{})).propose(run_id,key,redact(context),PROPOSAL_INSTRUCTIONS,PROPOSAL_FIELDS)
        if proposal['kind']!=body['kind']:raise ValueError('model_changed_requested_artifact_kind')
        return self.propose(run_id,{**proposal,'parent_id':parent})

    def validate(self,run_id,ident,suite_id):
        contract=self.allowed(run_id); value=self.get(run_id,ident); artifact=value['artifact']
        validate_artifact(artifact,contract)
        suite=self.settings.get('validation_suites',{}).get(suite_id)
        if not suite or suite.get('scope')!=contract['scope'] or suite.get('independent') is not True or suite.get('hidden_from_model') is not True or not suite.get('source_ref'):
            raise ValueError('host_owned_independent_hidden_validation_suite_required')
        if set(suite.get('case_ids',[])) & set(suite.get('training_case_ids',[])):raise ValueError('heldout_cases_overlap_training')
        cases=suite.get('cases',[])
        if not isinstance(cases,list) or len(cases)<suite.get('minimum_cases',3) or len(cases)>100 or len(set(c['id'] for c in cases))!=len(cases) or set(c['id'] for c in cases)!=set(suite.get('case_ids',[])):
            raise ValueError('complete_independent_case_denominator_required')
        parent=self.get(run_id,artifact['parent_id'])['artifact'] if artifact['parent_id'] else None
        rows=[]
        for case in cases:
            row={'case_id':case['id'],'input_digest':digest(case['input']),'expected_digest':digest(case['expected'])}
            for label,item in (('candidate',artifact),('baseline',parent)):
                if not item:row[label]={'passed':False,'steps':None,'error':'no_original_baseline'};continue
                try:
                    result=isolated({'operation':'validate_case','artifact':item,'contract':contract,'case':case})
                    row[label]={'passed':result['passed'],'output_digest':result['output_digest'],'steps':result.get('steps'),'host_replay_only':result['host_replay_only']}
                except (ValueError,subprocess.TimeoutExpired):row[label]={'passed':False,'steps':None,'error':'execution_rejected'}
            rows.append(row)
        all_candidate=all(r['candidate']['passed'] for r in rows)
        reliability_improved=parent is not None and all_candidate and any(not r['baseline']['passed'] for r in rows)
        simplified=all_candidate and all(r['baseline']['passed'] and r['candidate']['steps']<r['baseline']['steps'] for r in rows)
        receipt={'schema':SCHEMA,'artifact_id':ident,'parent_id':artifact['parent_id'],'constraints_digest':digest(contract),
                 'suite_digest':digest(suite),'suite_id':suite_id,'source_class':'engine_executor','source_ref':suite['source_ref'],
                 'fixture':suite.get('fixture') is True,'independent':True,'checks':rows,'all_cases_pass':all_candidate,
                 'execution_improvement_verified':reliability_improved or simplified,'new_pure_capability_verified':parent is None and all_candidate,'simplification_verified':simplified,
                 'economic_adoption_proven':False,'created_at':self.store.clock()}
        receipt['id']=digest(receipt)
        self.store.artifact(run_id,0,'evolution.validation.'+receipt['id'],receipt)
        return receipt

    def adopt(self,run_id,ident,receipt_id):
        contract=self.allowed(run_id); value=self.get(run_id,ident); artifact=value['artifact'];validate_artifact(artifact,contract)
        receipt=self.store.artifact(run_id,0,'evolution.validation.'+receipt_id)
        suite=self.settings.get('validation_suites',{}).get((receipt or {}).get('suite_id'))
        if not receipt or receipt['artifact_id']!=ident or receipt['constraints_digest']!=digest(contract) or not suite or receipt['suite_digest']!=digest(suite) or receipt.get('all_cases_pass') is not True or not (receipt.get('execution_improvement_verified') is True or receipt.get('new_pure_capability_verified') is True):
            raise ValueError('original_independent_execution_improvement_receipt_required')
        with self.store.transaction():
            head=self.head(run_id,artifact['kind'])
            if head['id']==ident:return {'state':'adopted_execution','id':ident,'already_adopted':True,'economic_adoption_proven':False}
            if head['id']!=artifact['parent_id'] or value['state']!='candidate':raise ValueError('original_parent_head_changed_or_candidate_not_available')
            value.update(state='adopted_execution',validation_receipt_id=receipt_id,adopted_at=self.store.clock())
            self.store.artifact(run_id,0,'evolution.artifact.'+ident,value)
            self.store.artifact(run_id,0,'evolution.artifact_head.'+artifact['kind'],{'id':ident,'previous':head['id'],'receipt_id':receipt_id})
        return {'schema':SCHEMA,'id':ident,'state':'adopted_execution','fixture':receipt['fixture'],'economic_adoption_proven':False}

    def rollback(self,run_id,ident,reason):
        self.allowed(run_id); value=self.get(run_id,ident); artifact=value['artifact']
        if not isinstance(reason,str) or not reason.strip():raise ValueError('rollback_reason_required')
        with self.store.transaction():
            head=self.head(run_id,artifact['kind'])
            if head['id']!=ident:raise ValueError('only_current_original_head_can_be_rolled_back')
            previous=artifact['parent_id']
            value.update(state='rolled_back',rollback_reason=reason,rolled_back_at=self.store.clock())
            self.store.artifact(run_id,0,'evolution.artifact.'+ident,value)
            self.store.artifact(run_id,0,'evolution.artifact_head.'+artifact['kind'],{'id':previous,'previous':None})
        return {'id':ident,'state':'rolled_back','head':previous,'economic_adoption_proven':False}

    def execute(self,run_id,ident,arguments):
        contract=self.allowed(run_id); value=self.get(run_id,ident);artifact=value['artifact']
        if self.head(run_id,artifact['kind'])['id']!=ident or value['state'] not in ('original','adopted_execution'):raise ValueError('only_original_or_independently_adopted_head_executes')
        op='execute' if artifact['kind']=='program' else 'graph_next'
        try:
            result=isolated({'operation':op,'artifact':artifact,'contract':contract,'arguments':arguments})
        except (ValueError,subprocess.TimeoutExpired) as exc:
            self.store.artifact(run_id,0,'evolution.execution_failure.'+ident,{'id':ident,'input_digest':digest(arguments),'error_type':type(exc).__name__,'created_at':self.store.clock()})
            if value['state']=='adopted_execution' and self.settings.get('rollback_on_failure') is True:
                self.rollback(run_id,ident,'independent_pure_execution_failure:'+type(exc).__name__)
            raise
        receipt={'id':ident,'source_class':'engine_executor','input_digest':digest(arguments),'result_digest':digest(result),'created_at':self.store.clock(),'result':result}
        self.store.artifact(run_id,0,'evolution.execution.'+digest(receipt),receipt)
        return result

    def transfer_candidate(self,run_id,method_id,grant_id):
        contract=self.allowed(run_id)
        original=self.settings.get('transfer_artifacts',{}).get(method_id)
        grant=self.settings.get('transfer_grants',{}).get(grant_id)
        if not original or not grant:raise ValueError('original_transfer_source_and_explicit_rights_required')
        source=original.get('artifact',{});proof=original.get('economic_adoption',{})
        if source.get('id')!=method_id or source.get('scope',{}).get('product_id')==contract['scope']['product_id'] or source.get('id')!=digest(source.get('body')):
            raise ValueError('different_original_product_method_required')
        if proof.get('method_id')!=method_id or proof.get('scope')!=source.get('scope') or proof.get('source_class')!='original_commercial_executor' or proof.get('settled_full_costs') is not True or proof.get('adopted') is not True or not proof.get('receipt_digest') or proof.get('fixture') is not False:
            raise ValueError('actual_original_economic_adoption_required')
        if grant.get('source_scope')!=source.get('scope') or grant.get('target_scope')!=contract['scope'] or grant.get('method_id')!=method_id or grant.get('allow_strategy_transfer') is not True or grant.get('allow_customer_data') is not False or grant.get('revoked') is not False or grant.get('expires_at',0)<=self.store.clock() or not grant.get('source_ref'):
            raise ValueError('precise_unexpired_transfer_rights_required')
        candidate=self.propose(run_id,{'kind':source['kind'],'body':source['body'],'hypothesis':'Held-out target test of '+method_id,'source_refs':[grant['source_ref']]})
        self.store.artifact(run_id,0,'evolution.transfer_origin.'+candidate['artifact']['id'],{'source_method_id':method_id,'source_product_id':source['scope']['product_id'],'grant_id':grant_id,'grant_digest':digest(grant),'proof_digest':digest(proof),'target_scope':contract['scope']})
        return {**candidate,'transfer_only_candidate':True,'cross_product_lift_proven':False}

    def transfer_evaluate(self,run_id,trial_id):
        from .artifact_kernel import evaluate_transfer_trial
        contract=self.allowed(run_id)
        # Authenticated host registries only. API/model cannot supply aggregate
        # successes or replacement expected outputs through this endpoint.
        trial=self.settings.get('transfer_trials',{}).get(trial_id)
        if not trial or trial.get('target_scope')!=contract['scope']:raise ValueError('original_target_scoped_transfer_trial_required')
        origin=self.store.artifact(run_id,0,'evolution.transfer_origin.'+trial.get('candidate_id',''))
        grant=self.settings.get('transfer_grants',{}).get((origin or {}).get('grant_id'))
        if not origin or not grant or grant.get('revoked') is not False or grant.get('expires_at',0)<=self.store.clock() or digest(grant)!=origin['grant_digest']:
            raise ValueError('original_transfer_rights_expired_revoked_or_changed')
        if trial['plan'].get('source_product_id')!=origin['source_product_id'] or trial['plan'].get('source_method_id')!=origin['source_method_id'] or trial['plan'].get('candidate_method_id')!=trial['candidate_id'] or trial['plan'].get('target_product_id')!=contract['scope']['product_id']:
            raise ValueError('original_source_target_method_bindings_required')
        result=evaluate_transfer_trial(trial['plan'],trial['original_rows'],self.store.clock())
        self.store.artifact(run_id,0,'evolution.transfer_result.'+trial_id,result)
        return result

    def tick(self,run_id):
        """One bounded learning transition on the existing host tick, no daemon."""
        import uuid
        if self.settings.get('autonomous') is not True:return {'state':'disabled','extra_worker_created':False}
        self.allowed(run_id)
        self.store.db.execute('CREATE TABLE IF NOT EXISTS artifact_evolution_locks(run_id TEXT PRIMARY KEY,token TEXT NOT NULL,expires REAL NOT NULL)')
        token=uuid.uuid4().hex;now=self.store.clock()
        with self.store.transaction():
            changed=self.store.db.execute('INSERT INTO artifact_evolution_locks VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET token=excluded.token,expires=excluded.expires WHERE artifact_evolution_locks.expires<=?',(run_id,token,now+180,now)).rowcount
        if not changed:return {'state':'controller_busy','extra_worker_created':False}
        try:
            kinds=self.settings.get('autonomous_kinds',['program'])
            maximum=self.settings.get('maximum_proposals',1)
            if not isinstance(kinds,list) or set(kinds)-{'program','graph'} or type(maximum) is not int or not 1<=maximum<=100:raise ValueError('bounded_original_autonomous_policy_required')
            for row in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'evolution.artifact.%' ORDER BY created",(run_id,)).fetchall():
                value=json.loads(row[0]);artifact=value['artifact']
                if value['state']!='candidate' or artifact['kind'] not in kinds:continue
                suite_id=self.settings.get('autonomous_suites',{}).get(artifact['kind'])
                if not suite_id:return {'state':'waiting_independent_validation','artifact_id':artifact['id'],'economic_adoption_proven':False}
                receipt=self.validate(run_id,artifact['id'],suite_id)
                if receipt['all_cases_pass'] and (receipt['execution_improvement_verified'] or receipt.get('new_pure_capability_verified')):
                    return self.adopt(run_id,artifact['id'],receipt['id'])
                value.update(state='quarantined',validation_receipt_id=receipt['id'])
                self.store.artifact(run_id,0,'evolution.artifact.'+artifact['id'],value)
                return {'state':'quarantined','artifact_id':artifact['id'],'economic_adoption_proven':False}
            used=self.store.db.execute("SELECT count(*) FROM artifacts WHERE run_id=? AND kind LIKE 'evolution.model.artifact.%'",(run_id,)).fetchone()[0]
            if used>=maximum:return {'state':'proposal_limit_reached','economic_adoption_proven':False}
            kind=next((k for k in kinds if self.settings.get('autonomous_source_refs',{}).get(k)),None)
            if kind is None:return {'state':'waiting_original_experience','economic_adoption_proven':False}
            return self.generate(run_id,{'kind':kind,'source_refs':self.settings['autonomous_source_refs'][kind]})
        finally:self.store.db.execute('DELETE FROM artifact_evolution_locks WHERE run_id=? AND token=?',(run_id,token))
