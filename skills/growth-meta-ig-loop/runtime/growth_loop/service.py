"""One model-neutral RPC contract over CLI, authenticated HTTP and MCP stdio.

A worker credential authenticates a trusted host, not the truth of an upstream
platform. Deploy only with hosts trusted to perform and attest connector reads.
"""
import hashlib
import hmac
import json
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from .files import canonical, digest
from .errors import WorkflowError
from .store import Store
from .workflows import WorkflowRunner, record_workflow_receipt
from .contracts import CAPABILITIES
from .graph import GraphRuntime, catalog
from .learning import Learning
from .actions import ActionManager
from .inbound import ingest_signed
from .mcp_protocol import MCP_VERSIONS
from .portability import local_path

PROTOCOL = 'growth-worker-v1'
METHODS = ['campaign_research', 'campaign_build', 'campaign_read', 'campaign_events', 'negotiate', 'status', 'read_public_source', 'delivery_report', 'execution_metrics', 'cost_summary', 'economic_receipt_ingest', 'interaction_receipt_ingest', 'trust_status', 'learning_status', 'commercial_next_experiment', 'commercial_adopt', 'commercial_monitor', 'commercial_transfer_register', 'commercial_transfer_compare', 'acquire', 'heartbeat', 'complete', 'action_prepare', 'action_result',
           'preflight', 'contract', 'run_create', 'execution_catalog', 'goal_readiness', 'tool_probe', 'tool_execute', 'tool_reconcile', 'source_ingest', 'source_retry', 'event_ingest', 'memory_search', 'memory_add', 'method_propose', 'experiment_register',
           'experiment_compare', 'method_monitor', 'method_adopt', 'method_rollback', 'graph', 'repair', 'stop', 'channel_recover', 'goal_summary', 'catalog',
           'capability_inventory', 'capability_discover', 'capability_readiness', 'plan_compose', 'plan_apply',
           'experience_sync', 'experience_search', 'procedure_derive', 'procedure_register', 'procedure_compare',
           'procedure_validate', 'procedure_adopt', 'procedure_rollback',
           'goal_experiment_register', 'goal_experiment_compare', 'goal_method_adopt', 'resume', 'pursuit_set', 'pursuit_status',
           'evolution_tick', 'evolution_status', 'commercial_offer', 'commercial_rank',
           'commercial_design', 'commercial_validate', 'commercial_register', 'commercial_compare', 'commercial_transfer', 'commercial_estimate',
           'artifact_contract','artifact_propose','artifact_generate','artifact_validate','artifact_adopt','artifact_rollback','artifact_execute','artifact_dispatch','artifact_transfer','artifact_transfer_evaluate','artifact_tick']


class Service:
    def __init__(self, store, worker, config):
        self.store, self.worker, self.config = store, worker, config
        store.db.execute('''CREATE TABLE IF NOT EXISTS worker_leases(
            task_key TEXT PRIMARY KEY, worker TEXT NOT NULL, lease TEXT NOT NULL,
            request_digest TEXT NOT NULL, expires REAL NOT NULL)''')

    def inventory(self,run_id):
        result=list(self.config.get('tool_inventory',[]))
        for row in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'tool.probe.%'",(run_id,)):
            value=json.loads(row[0]);inventory=value['inventory']
            route=self.config.get('tool_routes',{}).get(inventory['tool_id'])
            if route and route.get('enabled') is True and digest(route)==value['route_sha256']:
                result.append(inventory)
        return result

    def run(self, method, params):
        # Instrumentation stays in the same kernel and cannot hide an operation
        # result. Unknown/crashed invocations remain explicitly unknown.
        from .observability import ExecutionMetrics
        metrics = ExecutionMetrics(self.store,self.config)
        observed = method not in ('execution_metrics','cost_summary','trust_status','learning_status','delivery_report') and isinstance(params,dict) and params.get('run_id') in self.config.get('runs',[])
        mark = metrics.begin(params['run_id'],self.worker,method,params) if observed else None
        try:
            result = self._run(method,params)
        except BaseException as exc:
            if mark:
                try: metrics.finish(mark,error=exc)
                except Exception: pass
            raise
        if mark:
            try: metrics.finish(mark,result=result)
            except Exception: pass
        return result

    def _run(self, method, params):
        if method not in METHODS or method not in self.config.get('methods', METHODS):
            raise ValueError('不支援／未授權的方法')
        schema = input_schema(method)
        if not isinstance(params, dict) or set(params) - set(schema['properties']) or any(k not in params for k in schema['required']):
            raise ValueError('工作契約欄位不完整或包含未知欄位')
        validate_params(schema, params)
        run_id = params['run_id']
        if run_id not in self.config.get('runs', []):
            raise WorkflowError('worker不屬於此品牌工作。', 'scope_mismatch')
        if method == 'run_create':
            from .workflows import validate_workflow_profile
            if self.config.get('allow_run_create') is not True:raise WorkflowError('此worker未開放建立工作。','scope_mismatch')
            candidate=json.loads(canonical(params['profile']))
            if candidate.get('mode','prepare')!='prepare':raise ValueError('工具建立工作先用prepare；登入與對外範圍另核對')
            if candidate.get('brand',{}).get('url') not in self.config.get('brand_urls',[]):raise WorkflowError('品牌不在本worker可建立範圍。','scope_mismatch')
            validate_workflow_profile(candidate)
            if candidate.get('pursuit',{}).get('enabled') is True and self.config.get('allow_goal_continuity') is not True:
                raise WorkflowError('此worker沒有延續目標的本人授權。','scope_mismatch')
            defer_seed=params.get('defer_seed',False)
            if defer_seed and (candidate.get('workflow',{}).get('id')!='goal' or params.get('proposal') is not None):
                raise ValueError('defer_seed只用於先探測工具、再組合的goal工作；不與proposal同時使用')
            exists=self.store.db.execute('SELECT config FROM runs WHERE id=?',(run_id,)).fetchone()
            if exists:
                old=json.loads(exists[0]);old.pop('_run_id',None)
                if digest(old)!=digest(candidate):raise WorkflowError('同一run_id已有不同設定，不能覆寫。','payload_changed')
                return {'run_id':run_id,'already_created':True,'external_actions':False}
            runner=WorkflowRunner(self.store)
            with self.store.transaction():
                self.store.create_run(candidate,run_id)
                from .capabilities import CapabilityBroker
                self.store.artifact(run_id,0,'capability.current',CapabilityBroker(self.store,self.config).inventory(run_id))
                if params.get('proposal') is not None:
                    from .adaptation import AdaptivePlanner
                    planner=AdaptivePlanner(self.store,self.config)
                    plan=planner.compose(run_id,1,params['proposal'])
                    planner.apply(run_id,plan['plan_id'])
                if not defer_seed:runner.seed(run_id)
            return {'run_id':run_id,'mode':'prepare','external_actions':False,'workflow_seeded':not defer_seed,
                    'next':'capability_discover, tool_probe, plan_compose, plan_apply' if defer_seed else 'preflight'}
        if method == 'contract':
            from .onboarding import host_contract
            return host_contract()
        profile = self.store.config(run_id)
        if method.startswith('campaign_'):
            from .campaign import Campaign
            campaign = Campaign(self.store, self.config)
            if method == 'campaign_research': return campaign.research(run_id, params['request_id'], params['urls'], params.get('receipt_ids'), params.get('fragments'))
            if method == 'campaign_build': return campaign.build(run_id, params['request_id'], params['source_request_id'], params['body'])
            if method == 'campaign_read': return campaign.read(run_id, params['artifact_id'])
            if method == 'campaign_events': return campaign.events(run_id, params.get('after', 0), params.get('limit', 50))
        if method in ('execution_metrics','interaction_receipt_ingest'):
            from .observability import ExecutionMetrics
            metrics=ExecutionMetrics(self.store,self.config)
            return metrics.summary(run_id) if method=='execution_metrics' else metrics.import_human_receipt(run_id,params['receipt_id'])
        if method in ('cost_summary','economic_receipt_ingest'):
            from .economics import Economics
            costs=Economics(self.store,self.config)
            return costs.summary(run_id) if method=='cost_summary' else costs.ingest(run_id,params['receipt_id'])
        if method in ('trust_status','learning_status'):
            from .trust import trust_status,learning_status
            return trust_status(self.store,self.config,run_id) if method=='trust_status' else learning_status(self.store,self.config,run_id)
        if method == 'read_public_source':
            from .standalone import read_public_source
            return read_public_source(self.store, self.config, run_id, params['url'],params.get('compact',False),params.get('request_id'))
        if method == 'delivery_report':
            from .standalone import delivery_report
            return delivery_report(self.store, run_id,self.config)
        if method.startswith('artifact_'):
            from .artifact_evolution import ArtifactEvolution
            from .artifact_policy import proposal_contract
            engine=ArtifactEvolution(self.store,self.config)
            if method=='artifact_contract':
                from . import __version__
                return {**proposal_contract(),'engine_version':__version__,'contract':engine.contract(run_id),'http':'/v1/rpc','mcp':'stdio','mcp_streamable_http':'/mcp (independent optional SDK gateway)','arbitrary_native_code':False}
            if method=='artifact_propose':return engine.propose(run_id,params['body'])
            if method=='artifact_generate':return engine.generate(run_id,params['body'])
            if method=='artifact_validate':return engine.validate(run_id,params['artifact_id'],params['suite_id'])
            if method=='artifact_adopt':return engine.adopt(run_id,params['artifact_id'],params['receipt_id'])
            if method=='artifact_rollback':return engine.rollback(run_id,params['artifact_id'],params['reason'])
            if method=='artifact_tick':return engine.tick(run_id)
            if method=='artifact_transfer':return engine.transfer_candidate(run_id,params['artifact_id'],params['grant_id'])
            if method=='artifact_transfer_evaluate':return engine.transfer_evaluate(run_id,params['trial_id'])
            if method=='artifact_execute':return engine.execute(run_id,params['artifact_id'],params['arguments'])
            task,request=self.owned(run_id,params)
            from .tooling import ToolExecutor
            route=ToolExecutor(self.store,self.config).route(params['route_id'])
            contract=engine.contract(run_id)
            if route.get('mode')!='read' or contract.get('argument_transform') is not True or contract.get('route_id')!=params['route_id'] or contract.get('route_digest')!=digest(route):
                raise ValueError('generated_dispatch_needs_original_pinned_read_route')
            result=engine.execute(run_id,params['artifact_id'],params['arguments'])
            if result.get('state')!='executed_pure_program':raise ValueError('generated_dispatch_requires_program_output')
            return ToolExecutor(self.store,self.config).execute(request,params['route_id'],result['output'],None,lease_task=task)
        if method.startswith('commercial_'):
            from .commercial import CommercialLearning
            from .commercial_contract import rankCommercialActions, designCommercialExperiment, validateCommercialMethod
            settings = self.config.get('commercial', {})
            commercial = CommercialLearning(self.store, settings)
            scope = commercial.scope(run_id)
            if method == 'commercial_offer':
                return commercial.compile_offer(run_id, params['body'])
            if method=='commercial_next_experiment':return commercial.next_experiment(run_id,params['body'],self.config)
            if method=='commercial_adopt':return commercial.adopt(run_id,params['experiment_id'],self.config)
            if method=='commercial_monitor':return commercial.monitor(run_id,params['experiment_id'],params['observations'],self.config)
            if method=='commercial_transfer_register':return commercial.transfer_register(run_id,params['body'],self.config)
            if method=='commercial_transfer_compare':return commercial.transfer_compare(run_id,params['trial_id'],params['observations'],self.config)
            if method == 'commercial_estimate':
                from .action_estimator import estimateCommercialActionValues
                body = params['body']
                if any(body.get(k) != scope[k] for k in scope):
                    raise WorkflowError('預測資料不屬於本品牌產品。', 'scope_mismatch')
                result = estimateCommercialActionValues(body, self.store.clock(), settings.get('trusted_observations', {}), settings.get('value_transfer_rights', []), settings.get('trusted_denominators', {}))
                self.store.artifact(run_id, 0, 'commercial.estimates.' + digest(result), result)
                return result
            if method in ('commercial_rank', 'commercial_design', 'commercial_validate'):
                body = params['body']
                if any(body.get(k) != scope[k] for k in ('tenant_id', 'brand_id')) or ('product_id' in body and body['product_id']!=scope['product_id']):
                    raise WorkflowError('商業提案不屬於本品牌工作。', 'scope_mismatch')
                if method == 'commercial_rank':
                    return rankCommercialActions(body, self.store.clock())
                if method == 'commercial_design':
                    result = designCommercialExperiment(body, self.store.clock())
                    self.store.artifact(run_id, 0, 'commercial.plan.' + digest(result), result)
                    return result
                return validateCommercialMethod(body, {**settings.get('method_constraints', {}), **scope})
            if method == 'commercial_compare':
                return commercial.compare(run_id, params['experiment_id'], params['observations'],self.config)
            if method == 'commercial_register':
                return commercial.register(run_id, params['body'],self.config)
            candidate = settings.get('transfer_methods', {}).get(params['method_id'])
            if not candidate:
                raise ValueError('Transfer method must be loaded from trusted host state')
            return commercial.transfer(run_id, candidate, {**params['body'], **scope})
        if method in ('evolution_tick', 'evolution_status'):
            from .evolution import EvolutionController
            controller = EvolutionController(self.store, self.config)
            result=controller.tick(run_id) if method == 'evolution_tick' else controller.status(run_id)
            if method=='evolution_tick' and self.config.get('artifact_evolution',{}).get('autonomous') is True:
                from .artifact_evolution import ArtifactEvolution
                try:result['artifact_evolution']=ArtifactEvolution(self.store,self.config).tick(run_id)
                except Exception as exc:result['artifact_evolution']={'state':'diagnostic_required','detail':str(exc)[:600],'economic_adoption_proven':False}
            return result
        from .capabilities import CapabilityBroker
        broker=CapabilityBroker(self.store,self.config)
        self.store.artifact(run_id,0,'capability.current',broker.inventory(run_id))
        if method == 'capability_inventory': return broker.inventory(run_id)
        if method == 'capability_discover': return broker.discover(run_id,params['route_id'])
        if method == 'capability_readiness': return broker.readiness(run_id)
        if method in ('plan_compose','plan_apply'):
            from .adaptation import AdaptivePlanner
            planner=AdaptivePlanner(self.store,self.config)
            if method=='plan_compose': return planner.compose(run_id,params['cycle'],params.get('proposal'))
            with self.store.transaction():
                applied=planner.apply(run_id,params['plan_id'])
                if applied['cycle']==1 and not applied.get('already_applied'):
                    from .goals import GoalRunner
                    GoalRunner(self.store).seed(run_id,1)
                    applied['workflow_seeded']=True
            return applied
        if method.startswith(('experience_','procedure_')):
            from .experience import Experience
            experience=Experience(self.store,self.config)
            if method=='experience_sync': return experience.sync(run_id)
            if method=='experience_search': return experience.episodes(run_id,params.get('skill_id'),params.get('after',0),params.get('limit',100))
            if method=='procedure_derive': return experience.derive(run_id,params['episode_ids'],params.get('instruction'),params.get('program'))
            if method=='procedure_register': return experience.register(run_id,params['body'])
            if method=='procedure_compare': return experience.compare(run_id,params['experiment_id'])
            if method=='procedure_validate': return experience.validate(run_id,params['procedure_id'])
            if method=='procedure_adopt': return experience.adopt(run_id,params['experiment_id'])
            if method=='procedure_rollback': return experience.rollback(run_id,params['procedure_id'],params['reason'])
        if method in ('goal_experiment_register','goal_experiment_compare','goal_method_adopt'):
            from .outcome_learning import OutcomeLearning
            outcomes=OutcomeLearning(self.store)
            if method=='goal_experiment_register':
                trial_runs=params['body'].get('baseline_runs',[])+params['body'].get('candidate_runs',[])
                if set(trial_runs)-set(self.config.get('runs',[])):raise WorkflowError('實驗包含此worker未授權的工作。','scope_mismatch')
                return outcomes.register(run_id,params['body'])
            if method=='goal_experiment_compare':return outcomes.compare(run_id,params['experiment_id'])
            return outcomes.adopt(run_id,params['experiment_id'])
        if method == 'tool_probe':
            from .tooling import ToolExecutor
            return ToolExecutor(self.store,self.config).probe(run_id,params['route_id'])
        if method == 'tool_reconcile':
            from .tooling import ToolExecutor
            if params.get('lease'):
                task,request=self.owned(run_id,params)
                return ToolExecutor(self.store,self.config).reconcile(request,params['route_id'],params['operation_key'],lease_task=task)
            operation=self.store.operation(params['operation_key'])
            task=self.store.task(operation['task_key']) if operation else None
            if not task or task['run_id']!=run_id:
                raise WorkflowError('對帳不屬於這個工作。','scope_mismatch')
            if task['state']=='running' and (task.get('lease_until') or 0)>self.store.clock():
                raise WorkflowError('現有worker仍持有租約，先由同一worker對帳。','lease_lost')
            request=self.store.artifact(run_id,task['cycle'],'flow_request.'+digest(task['key'])[:20])
            if not request:raise WorkflowError('原操作請求不存在。','scope_mismatch')
            return ToolExecutor(self.store,self.config).reconcile(request,params['route_id'],params['operation_key'],lease_required=False)
        if method == 'execution_catalog':
            from .execution import execution_catalog
            return execution_catalog()
        if method == 'goal_readiness':
            from .execution import readiness
            from .files import read_json
            traces=[json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'tool.execution.%'",(run_id,))]
            grant_file=self.store.root/'authorization.json'
            return readiness(profile,self.config,traces,params.get('goal_id'),
                connected=(self.store.artifact(run_id,0,'connected_accounts') or {}).get('accounts',[]),
                authorization=read_json(grant_file) if grant_file.exists() else None,now=self.store.clock())
        if method == 'source_ingest':
            from .sources import SourceIngress
            return SourceIngress(self.store,self.config.get('data_sources',{})).ingest(run_id,params['source'],params['raw_body'].encode('utf-8'),params['headers'])
        if method == 'source_retry':
            from .sources import SourceIngress
            return SourceIngress(self.store,self.config.get('data_sources',{})).retry_pending(run_id,params.get('source'))
        if method == 'preflight':
            from .onboarding import preflight
            return preflight(self.store,run_id,self.config.get('capabilities',[]),params.get('inventory',self.inventory(run_id)),self.config.get('verification_max_age',3600))
        if method == 'negotiate':
            return {'protocol': PROTOCOL, 'capabilities': self.config.get('capabilities', []),
                    'methods': self.config.get('methods', METHODS), 'mcp_versions':MCP_VERSIONS, 'preflight_enforced':self.config.get('enforce_preflight',False), 'continuous_execution': 'requires_running_worker',
                    'platform_native_tools': 'provided_by_trusted_host', 'receipt_trust': 'authenticated_host_attestation_not_independent_platform_verification'}
        if method == 'status': return self.store.status(run_id)
        if method == 'pursuit_status':
            from .continuity import summary
            return summary(self.store,run_id) or {'enabled':False,'requires_actual_trusted_worker':True}
        if method == 'pursuit_set':
            if self.config.get('allow_goal_continuity') is not True:raise WorkflowError('此worker沒有延續目標的本人授權。','scope_mismatch')
            body=params['body']
            if set(body)-{'enabled','deadline_at'} or type(body.get('enabled')) is not bool:raise ValueError('pursuit設定需明確enabled及可選的原期限')
            if self.store.db.execute("SELECT 1 FROM tasks WHERE run_id=? AND state='running' AND lease_until>?",(run_id,self.store.clock())).fetchone():raise WorkflowError('原步驟正在執行，請保留其租約。','scope_mismatch')
            from .continuity import configure, summary, discover
            with self.store.transaction():
                value=configure(self.store,run_id,body['enabled'],body.get('deadline_at'))
                if body['enabled']:
                    row=self.store.db.execute("SELECT key FROM tasks WHERE run_id=? AND state='done' AND (kind LIKE 'flow.goal.evaluate.%' OR data LIKE '%goal_advance%') ORDER BY updated DESC,rowid DESC LIMIT 1",(run_id,)).fetchone()
                    if row:discover(self.store,self.store.task(row[0]),'owner_registered_continuity')
            return summary(self.store,run_id)
        if method == 'catalog':
            from .library_catalog import page
            return page(params)
        if method == 'goal_summary':
            evaluations=[json.loads(r[0]) for r in self.store.db.execute("SELECT data FROM artifacts WHERE run_id=? AND kind LIKE 'goal.evaluation.%' ORDER BY created,rowid",(run_id,))]
            return {'goal':self.store.artifact(run_id,0,'goal.spec'),'baselines':{r['kind']:json.loads(r['data']) for r in self.store.db.execute("SELECT kind,data FROM artifacts WHERE run_id=? AND cycle=0 AND kind LIKE 'goal.baseline.%'",(run_id,))},'latest':evaluations[-1] if evaluations else None,'status':self.store.status(run_id),'report_is_not_independent_sales_evidence':True}
        if method == 'channel_recover':
            from .channels import recover
            return recover(self.store,run_id,params['task_key'],params['code'],params['evidence'],params.get('retry_at'))
        if method == 'graph': return self.store.artifact(run_id, params.get('cycle', 1), 'graph.definition') or {'legacy': True}
        if method == 'stop': return GraphRuntime(self.store).stop(run_id, params['reason']) or {'stopped': True}
        if method == 'repair': return GraphRuntime(self.store).repair(run_id, params['task_key'], params['reason'])
        if method == 'acquire': return self.acquire(run_id)
        if method == 'resume': return self.resume(run_id,params['task_key'])
        if method in ('heartbeat', 'complete', 'action_prepare', 'action_result', 'tool_execute'):
            task, request = self.owned(run_id, params)
            if method == 'tool_execute':
                from .tooling import ToolExecutor
                return ToolExecutor(self.store,self.config).execute(request,params['route_id'],params.get('arguments'),params.get('action'),lease_task=task)
            if method == 'heartbeat':
                self.store.assert_lease(task, 180)
                self.store.db.execute('UPDATE worker_leases SET expires=? WHERE task_key=?', (self.store.clock() + 180, task['key']))
                return {'lease_until': self.store.clock() + 180}
            if method == 'action_prepare': return ActionManager(self.store).prepare(request, params['action'])
            if method == 'action_result':
                operation = self.store.operation(params['operation_key'])
                if not operation or operation['task_key'] != task['key']:
                    raise ValueError('操作不屬於此租約')
                return ActionManager(self.store).result(run_id, params['operation_key'], params['result'])
            record_workflow_receipt(self.store, request, params['receipt'])
            # Re-enter the existing executor so validation, state application and
            # graph transition cannot be bypassed by a worker saying "done".
            if params['receipt'].get('state') == 'waiting_external':
                self.store.db.execute('DELETE FROM worker_leases WHERE task_key=?', (task['key'],))
                return {'state': 'waiting_event'}
            result = WorkflowRunner(self.store).execute(task, profile)
            self.store.finish(task, 'done', result)
            self.store.db.execute('DELETE FROM worker_leases WHERE task_key=?', (task['key'],))
            return {'state': 'done', 'task_key': task['key'], 'result': result}
        if method == 'event_ingest':
            return ingest_signed(self.store, run_id, canonical(params['event']).encode(), params['headers'], params['source'])
        learning = Learning(self.store)
        if method == 'memory_search': return learning.search(run_id, params.get('kind'), params.get('after', 0), params.get('limit', 100))
        if method == 'memory_add': return learning.remember(run_id, params['kind'], params['body'])
        if method == 'method_propose': return learning.propose(run_id, params['body'])
        if method == 'experiment_register': return learning.preregister(run_id, params['body'])
        if method == 'experiment_compare': return learning.compare(run_id, params['experiment_id'], params['observations'])
        if method == 'method_monitor':
            if profile.get('mode')!='fixture':
                from .commercial import CommercialLearning
                return CommercialLearning(self.store,self.config.get('commercial',{})).monitor(run_id,params['experiment_id'],params['observations'],self.config)
            return learning.monitor(run_id, params['experiment_id'], params['observations'])
        if method == 'method_adopt':
            if profile.get('mode')!='fixture':
                from .commercial import CommercialLearning
                return CommercialLearning(self.store,self.config.get('commercial',{})).adopt(run_id,params['experiment_id'],self.config)
            return learning.adopt(run_id, params['experiment_id'])
        if method == 'method_rollback': return learning.rollback(run_id, params['reason'])
        raise ValueError('未知方法')

    def acquire(self, run_id):
        if (self.store.root / '.transfer.lock').exists():
            return {'work': None, 'reason': 'workspace_transfer_in_progress'}
        if self.store.artifact(run_id, 0, 'graph.stopped'):
            return {'work': None, 'reason': 'stopped'}
        if self.config.get('allow_evolution') is True and self.config.get('evolution', {}).get('enabled') is True:
            from .evolution import EvolutionController
            EvolutionController(self.store, self.config).tick(self.config['evolution'].get('control_run_id', run_id))
        WorkflowRunner(self.store).wake_due(run_id)
        # Scheduler creates requests but never impersonates a model/tool host.
        from .cli import build_engine
        build_engine(self.store, run_id).run_tick(run_id)
        now = self.store.clock()
        caps = set(self.config.get('capabilities', []))
        rows = self.store.db.execute("SELECT key FROM tasks WHERE run_id=? AND (state='waiting_agent' OR (state='running' AND lease_until<=?)) ORDER BY cycle,rowid", (run_id, now)).fetchall()
        blocked=[]
        for row in rows:
            task = self.store.task(row['key'])
            if not task['data'].get('skill_id'): continue
            if task['attempts'] > self.store.config(run_id).get('recovery', {}).get('max_attempts', 5):
                self.store.db.execute("UPDATE tasks SET state='waiting_owner',lease=NULL,lease_until=NULL WHERE key=?", (task['key'],))
                continue
            request = WorkflowRunner(self.store).request(task, self.store.config(run_id))
            if not set(request['needed_capabilities']).issubset(caps):continue
            if self.config.get('enforce_preflight'):
                from .onboarding import preflight
                readiness=preflight(self.store,run_id,caps,self.inventory(run_id),self.config.get('verification_max_age',3600),tasks=[task])
                if readiness['missing_capabilities'] or readiness['unverified_or_stale_tools'] or (request['expected_write_actions'] and not readiness['ready_for_execution']):
                    blocked.append({'task_key':task['key'],'diagnosis':readiness});continue
            lease = uuid.uuid4().hex
            with self.store.transaction():
                changed = self.store.db.execute("UPDATE tasks SET state='running',lease=?,lease_until=?,updated=? WHERE key=? AND (state='waiting_agent' OR (state='running' AND lease_until<=?))", (lease, now + 180, now, task['key'], now)).rowcount
                if not changed: continue
                self.store.db.execute('INSERT OR REPLACE INTO worker_leases VALUES(?,?,?,?,?)', (task['key'], self.worker, lease, request['request_sha256'], now + 180))
            from .files import read_json
            return {'protocol': PROTOCOL, 'lease': lease, 'lease_until': now + 180, 'request': request,
                    'context': read_json(request['context_file']),
                    'pending_operations':[{'operation_key':op['key'],'state':op['state'],'original_action':json.loads(op['request']),
                        'cursor':self.store.artifact(run_id,task['cycle'],'tool.write_cursor.'+digest(op['key']))}
                        for op in self.store.db.execute("SELECT key,state,request FROM operations WHERE task_key=? AND state IN ('in_flight','uncertain')",(task['key'],))]}
        return {'work': None, 'reason': 'preflight_blocked' if blocked else 'waiting_or_capability_mismatch','blocked_tasks':blocked}

    def owned(self, run_id, params):
        task = self.store.task(params['task_key'])
        row = self.store.db.execute('SELECT * FROM worker_leases WHERE task_key=?', (params['task_key'],)).fetchone()
        if not task or not row or task['run_id'] != run_id or row['worker'] != self.worker or row['lease'] != params.get('lease') or row['expires'] <= self.store.clock() or task['state'] != 'running' or task['lease'] != row['lease'] or task['lease_until'] <= self.store.clock():
            raise WorkflowError('租約過期、不是此worker或已被接回。', 'lease_lost')
        request = WorkflowRunner(self.store).request(task, self.store.config(run_id))
        if params.get('request_sha256') != row['request_digest'] or request['request_sha256'] != row['request_digest']:
            raise WorkflowError('工作內容摘要不相符。', 'payload_changed')
        return task, request

    def resume(self,run_id,task_key):
        """Reconnect this authenticated worker to its original unexpired lease."""
        task=self.store.task(task_key)
        row=self.store.db.execute('SELECT * FROM worker_leases WHERE task_key=?',(task_key,)).fetchone()
        if not task or task['run_id']!=run_id or not row or row['worker']!=self.worker or task['state']!='running' or row['lease']!=task['lease'] or min(row['expires'],task['lease_until'] or 0)<=self.store.clock():
            raise WorkflowError('只能接回本worker尚未到期的原租約；過期則由acquire正常續接。','lease_lost')
        params={'task_key':task_key,'lease':row['lease'],'request_sha256':row['request_digest']}
        task,request=self.owned(run_id,params)
        from .files import read_json
        pending=[{'operation_key':op['key'],'state':op['state'],'original_action':json.loads(op['request']),
                  'cursor':self.store.artifact(run_id,task['cycle'],'tool.write_cursor.'+digest(op['key']))}
                 for op in self.store.db.execute("SELECT key,state,request FROM operations WHERE task_key=? AND state IN ('in_flight','uncertain')",(task_key,))]
        attempts=[{'attempt_ref':a['kind'],**json.loads(a['data'])} for a in self.store.db.execute("SELECT kind,data FROM artifacts WHERE run_id=? AND kind LIKE 'tool.attempt.%'",(run_id,))
                  if json.loads(a['data']).get('task_key')==task_key and json.loads(a['data']).get('state')=='pending']
        return {'protocol':PROTOCOL,'lease':row['lease'],'lease_until':min(row['expires'],task['lease_until']),
                'request':request,'context':read_json(local_path(self.store,request['context_file'])),'pending_operations':pending,
                'unresolved_tool_attempts':attempts,
                'resumed_original_lease':True,'lease_extended':False}


def load_worker(config, worker):
    value = config.get('workers', {}).get(worker)
    if not value: raise ValueError('未設定worker')
    return value


def http_server(workspace, config, host='127.0.0.1', port=8766):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # Never log credentials or private bodies.
        def do_GET(self):
            if self.path!='/v1/manifest':self.send_error(404);return
            from . import __version__
            data=canonical({'product':'Nuluno Engine','engine_version':__version__,'api_version':'v1','http_rpc':'/v1/rpc','mcp_transport':'stdio','evolution_schema':'nuluno-evolution-v1','authentication_required':True,'arbitrary_native_code':False}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        def do_POST(self):
            try:
                if self.path.startswith('/sources/'):
                    parts=self.path.split('/')
                    if len(parts)!=4:raise ValueError('Invalid source route')
                    run_id,source=parts[2:]
                    length=int(self.headers.get('Content-Length',0))
                    if not 0<length<=512*1024:raise ValueError('Invalid source body size')
                    from .sources import SourceIngress
                    store=Store(workspace)
                    try:result=SourceIngress(store,config.get('data_sources',{})).ingest(run_id,source,self.rfile.read(length),dict(self.headers))
                    finally:store.close()
                    payload=canonical({'result':result}).encode()
                    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
                    return
                if self.path not in ('/rpc','/v1/rpc'): raise ValueError('未知路徑')
                worker = self.headers.get('X-Growth-Worker', '')
                settings = load_worker(config, worker)
                secret = os.environ.get(settings.get('token_env', ''), '')
                if not secret or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + secret):
                    self.send_error(401); return
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 2 * 1024 * 1024: raise ValueError('請求大小錯誤')
                body = json.loads(self.rfile.read(length))
                json.dumps(body, allow_nan=False)
                store = Store(workspace)
                try: result = Service(store, worker, settings).run(body['method'], body.get('params', {}))
                finally: store.close()
                payload, status = {'result': result}, 200
            except (ValueError, KeyError, TypeError, WorkflowError) as exc:
                from .files import redact
                payload, status = {'error': {'kind': getattr(exc, 'kind', 'invalid_request'), 'message': str(exc),'details':redact(getattr(exc,'details',{})),'retry_after':getattr(exc,'retry_after',None)}}, 400
            data = canonical(payload).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    return HTTPServer((host, port), Handler)


def serve(workspace, config, host='127.0.0.1', port=8766):
    with http_server(workspace, config, host, port) as server:
        server.serve_forever()


def mcp_stdio(workspace, config, worker):
    """Local stdio supports legacy handshake and modern per-request metadata."""
    import sys
    from .mcp_protocol import MCPServer,stdio_loop
    settings=load_worker(config,worker)
    def call(name,arguments):
        store=Store(workspace)
        try:return Service(store,worker,settings).run(name,arguments)
        finally:store.close()
    methods=[m for m in METHODS if m in settings.get('methods',METHODS)]
    stdio_loop(MCPServer(call,methods,input_schema),sys.stdin,sys.stdout)


def input_schema(method):
    fields = {'fragments':'array','receipt_ids':'array','urls':'array','source_request_id':'string','compact':'boolean','request_id':'string','url': 'string', 'task_key': 'string', 'lease': 'string', 'request_sha256': 'string', 'receipt': 'object',
              'action': 'object', 'operation_key': 'string', 'result': 'object', 'event': 'object',
              'headers': 'object', 'source': 'string', 'kind': 'string', 'body': 'object',
              'experiment_id': 'string', 'observations': 'object', 'reason': 'string',
              'profile':'object', 'inventory':'array', 'cycle': 'integer', 'after': 'integer', 'limit': 'integer', 'code': 'string', 'evidence': 'array', 'retry_at':'number',
              'route_id':'string','arguments':'object','goal_id':'string','raw_body':'string',
              'proposal':'object','plan_id':'string','skill_id':'string','episode_ids':'array','instruction':'string','procedure_id':'string','defer_seed':'boolean','program':'array','method_id':'string','artifact_id':'string','suite_id':'string','receipt_id':'string','grant_id':'string','trial_id':'string'}
    required = {
      'campaign_research':['request_id','urls'], 'campaign_build':['request_id','source_request_id','body'], 'campaign_read':['artifact_id'],
      'economic_receipt_ingest':['receipt_id'],'interaction_receipt_ingest':['receipt_id'],
      'commercial_next_experiment':['body'],'commercial_adopt':['experiment_id'],'commercial_monitor':['experiment_id','observations'],
      'commercial_transfer_register':['body'],'commercial_transfer_compare':['trial_id','observations'],
      'read_public_source':['url'],
      'artifact_propose':['body'],'artifact_generate':['body'],'artifact_validate':['artifact_id','suite_id'],
      'artifact_adopt':['artifact_id','receipt_id'],'artifact_rollback':['artifact_id','reason'],'artifact_execute':['artifact_id','arguments'],
      'artifact_transfer':['artifact_id','grant_id'],'artifact_transfer_evaluate':['trial_id'],
      'artifact_dispatch':['artifact_id','arguments','route_id','task_key','lease','request_sha256'],
      'run_create':['profile'],
      'commercial_offer':['body'], 'commercial_rank':['body'], 'commercial_design':['body'], 'commercial_estimate':['body'],
      'commercial_validate':['body'], 'commercial_register':['body'], 'commercial_compare':['experiment_id','observations'], 'commercial_transfer':['method_id','body'],
      'pursuit_set':['body'],
      'resume':['task_key'],
      'heartbeat': ['task_key','lease','request_sha256'],
      'complete': ['task_key','lease','request_sha256','receipt'],
      'action_prepare': ['task_key','lease','request_sha256','action'],
      'action_result': ['task_key','lease','request_sha256','operation_key','result'],
      'tool_execute':['task_key','lease','request_sha256','route_id'],
      'tool_reconcile':['route_id','operation_key'],
      'tool_probe':['route_id'],
      'capability_discover':['route_id'], 'plan_compose':['cycle'], 'plan_apply':['plan_id'],
      'procedure_derive':['episode_ids'], 'procedure_register':['body'], 'procedure_compare':['experiment_id'],
      'procedure_validate':['procedure_id'], 'procedure_adopt':['experiment_id'], 'procedure_rollback':['procedure_id','reason'],
      'goal_experiment_register':['body'], 'goal_experiment_compare':['experiment_id'], 'goal_method_adopt':['experiment_id'],
      'source_ingest':['source','raw_body','headers'],
      'event_ingest': ['event','headers','source'], 'memory_add': ['kind','body'],
      'method_propose': ['body'], 'experiment_register': ['body'],
      'experiment_compare': ['experiment_id','observations'], 'method_monitor':['experiment_id','observations'], 'method_adopt': ['experiment_id'],
      'channel_recover':['task_key','code','evidence'], 'method_rollback': ['reason'], 'repair': ['task_key','reason'], 'stop': ['reason']}.get(method, [])
    optional = {'campaign_research':['receipt_ids','fragments'],'campaign_events':['after','limit'],'catalog':['kind','skill_id','after','limit'],'read_public_source':['compact','request_id'],'run_create':['proposal','defer_seed'], 'plan_compose':['proposal'], 'experience_search':['skill_id','after','limit'], 'procedure_derive':['instruction','program'],
                'preflight':['inventory'], 'goal_readiness':['goal_id'], 'tool_execute':['arguments','action'], 'tool_reconcile':['task_key','lease','request_sha256'], 'source_retry':['source'], 'memory_search': ['kind','after','limit'], 'graph': ['cycle'], 'channel_recover':['retry_at']}.get(method, [])
    return {'$schema':'https://json-schema.org/draft/2020-12/schema', 'type': 'object', 'properties': {'run_id': {'type': 'string'}, **{f: {'type': fields[f]} for f in required + optional}},
            'required': ['run_id'] + required, 'additionalProperties': False}


def validate_params(schema, params):
    import math
    expected={'string':str,'object':dict,'array':list,'integer':int,'boolean':bool}
    for key,value in params.items():
        kind=schema['properties'][key]['type']
        if kind=='number':valid=type(value) in (int,float) and math.isfinite(value)
        else:valid=type(value) is expected[kind]
        if not valid:raise ValueError('Invalid field type: '+key)
    json.dumps(params,allow_nan=False)
