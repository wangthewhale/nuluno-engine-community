"""Small dependency-free client for the package's authenticated custom HTTP RPC."""
import json
import os
from urllib.parse import urlparse
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from .transport import NoRedirect
from .files import redact


class GrowthRPCError(ValueError):
    def __init__(self,error):
        super().__init__(error.get('message','RPC request failed'))
        self.kind=error.get('kind','invalid_request')
        self.details=error.get('details',{})
        self.retry_after=error.get('retry_after')


class GrowthClient:
    def __init__(self,url,worker,token_env,timeout=60,api_version='v1'):
        parsed=urlparse(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in ('https','http') or (parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','localhost','::1')):raise ValueError('RPC needs HTTPS, or local HTTP, without embedded credentials')
        
        if api_version not in ('v1','legacy'):raise ValueError('Unsupported engine API version')
        self.url=url.rstrip('/')+('/v1/rpc' if api_version=='v1' else '/rpc');self.worker=worker;self.token_env=token_env;self.timeout=timeout

    def call(self,method,**params):
        secret=os.environ.get(self.token_env)
        if not secret:raise ValueError('Missing operator token environment variable')
        request=Request(self.url,data=json.dumps({'method':method,'params':params},allow_nan=False).encode(),headers={'Content-Type':'application/json','X-Growth-Worker':self.worker,'Authorization':'Bearer '+secret})
        try:
            with build_opener(NoRedirect).open(request,timeout=self.timeout) as response:value=json.load(response)
        except HTTPError as exc:
            if exc.code==401:raise ValueError('Worker authentication failed') from None
            try:value=json.load(exc)
            except (ValueError,TypeError):raise ValueError('RPC request rejected') from None
        if 'error' in value:raise GrowthRPCError(redact(value['error'],[secret]))
        return value['result']

    def acquire(self,run_id):return self.call('acquire',run_id=run_id)
    def resume(self,run_id,task_key):return self.call('resume',run_id=run_id,task_key=task_key)

    @staticmethod
    def ownership(job):
        request=job['request']
        return {'run_id':request['run_id'],'task_key':request['task_key'],'lease':job['lease'],'request_sha256':request['request_sha256']}

    def heartbeat(self,job):return self.call('heartbeat',**self.ownership(job))
    def prepare(self,job,action):return self.call('action_prepare',**self.ownership(job),action=action)
    def reconcile(self,job,operation_key,result):return self.call('action_result',**self.ownership(job),operation_key=operation_key,result=result)
    def complete(self,job,receipt):return self.call('complete',**self.ownership(job),receipt=receipt)
    def tool_probe(self,run_id,route_id):return self.call('tool_probe',run_id=run_id,route_id=route_id)
    def capability_inventory(self,run_id):return self.call('capability_inventory',run_id=run_id)
    def capability_discover(self,run_id,route_id):return self.call('capability_discover',run_id=run_id,route_id=route_id)
    def plan_compose(self,run_id,cycle,proposal=None):
        return self.call('plan_compose',run_id=run_id,cycle=cycle,**({'proposal':proposal} if proposal is not None else {}))
    def plan_apply(self,run_id,plan_id):return self.call('plan_apply',run_id=run_id,plan_id=plan_id)
    def procedure_derive(self,run_id,episode_ids,instruction=None,program=None):
        return self.call('procedure_derive',run_id=run_id,episode_ids=episode_ids,**({'instruction':instruction} if instruction is not None else {}),**({'program':program} if program is not None else {}))
    def evolution_tick(self,run_id):return self.call('evolution_tick',run_id=run_id)
    def evolution_status(self,run_id):return self.call('evolution_status',run_id=run_id)
    def tool_execute(self,job,route_id,arguments=None,action=None):
        values={'route_id':route_id}
        if arguments is not None:values['arguments']=arguments
        if action is not None:values['action']=action
        return self.call('tool_execute',**self.ownership(job),**values)
    def tool_reconcile(self,run_id,route_id,operation_key,job=None):
        ownership=self.ownership(job) if job else {'run_id':run_id}
        return self.call('tool_reconcile',**ownership,route_id=route_id,operation_key=operation_key)

    def artifact_contract(self,run_id):return self.call('artifact_contract',run_id=run_id)
    def artifact_generate(self,run_id,kind,source_refs):return self.call('artifact_generate',run_id=run_id,body={'kind':kind,'source_refs':source_refs})
    def artifact_propose(self,run_id,body):return self.call('artifact_propose',run_id=run_id,body=body)
    def artifact_validate(self,run_id,artifact_id,suite_id):return self.call('artifact_validate',run_id=run_id,artifact_id=artifact_id,suite_id=suite_id)
    def artifact_adopt(self,run_id,artifact_id,receipt_id):return self.call('artifact_adopt',run_id=run_id,artifact_id=artifact_id,receipt_id=receipt_id)
    def artifact_execute(self,run_id,artifact_id,arguments):return self.call('artifact_execute',run_id=run_id,artifact_id=artifact_id,arguments=arguments)
    def artifact_rollback(self,run_id,artifact_id,reason):return self.call('artifact_rollback',run_id=run_id,artifact_id=artifact_id,reason=reason)

    def artifact_tick(self,run_id):return self.call('artifact_tick',run_id=run_id)
    def artifact_transfer(self,run_id,artifact_id,grant_id):return self.call('artifact_transfer',run_id=run_id,artifact_id=artifact_id,grant_id=grant_id)
    def artifact_transfer_evaluate(self,run_id,trial_id):return self.call('artifact_transfer_evaluate',run_id=run_id,trial_id=trial_id)
