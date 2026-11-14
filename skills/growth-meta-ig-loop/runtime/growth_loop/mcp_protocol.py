"""Dual-era MCP stdio framing; model and platform credentials remain host-owned."""
import json
from .files import canonical
from .errors import WorkflowError

MCP_VERSIONS=['2026-07-28','2025-11-25','2025-06-18']
from . import __version__
SERVER_INFO={'name':'nuluno-engine','version':__version__}
PREFIX='io.modelcontextprotocol/'


class ProtocolError(ValueError):
    def __init__(self,message,code=-32602,data=None):
        super().__init__(message);self.code=code;self.data=data


class MCPServer:
    def __init__(self, call, methods, schema):
        self.call,self.methods,self.schema=call,methods,schema
        self.legacy_version=None;self.legacy_ready=False

    def process(self,message):
        ident=message.get('id') if isinstance(message,dict) else None
        modern=False
        try:
            if not isinstance(message,dict) or message.get('jsonrpc')!='2.0' or not isinstance(message.get('method'),str):raise ProtocolError('Invalid JSON-RPC request',-32600)
            if 'id' not in message:
                if message['method']=='notifications/initialized' and self.legacy_version:self.legacy_ready=True
                return None
            if type(ident) not in (str,int):raise ProtocolError('Request ID must be a string or integer',-32600)
            params=message.get('params',{})
            if not isinstance(params,dict):raise ProtocolError('params must be an object')
            method=message['method'];meta=params.get('_meta',{})
            if not isinstance(meta,dict):raise ProtocolError('_meta must be an object')
            version=meta.get(PREFIX+'protocolVersion')
            if version is None and PREFIX+'clientCapabilities' in meta:raise ProtocolError('Missing required per-request protocolVersion')
            if version is not None:
                modern=True
                if version!='2026-07-28':raise ProtocolError('Unsupported protocol version',-32022,{'supported':MCP_VERSIONS,'requested':version})
                if not isinstance(meta.get(PREFIX+'clientCapabilities'),dict):raise ProtocolError('Missing required per-request clientCapabilities')
            if method=='initialize':
                requested=params.get('protocolVersion')
                if not isinstance(requested,str) or not isinstance(params.get('capabilities'),dict) or not isinstance(params.get('clientInfo'),dict):raise ProtocolError('initialize requires protocolVersion, capabilities and clientInfo')
                self.legacy_version=requested if requested in MCP_VERSIONS[1:] else MCP_VERSIONS[1]
                self.legacy_ready=False
                value={'protocolVersion':self.legacy_version,'capabilities':{'tools':{}},'serverInfo':SERVER_INFO,'instructions':'Call preflight before execution. Tools require an explicitly scoped run; platform access comes from the trusted host.'}
            else:
                if not modern and not self.legacy_ready and method!='ping':raise ProtocolError('Use modern per-request metadata or complete legacy initialization',-32600)
                if method=='server/discover':
                    if not modern:raise ProtocolError('server/discover uses modern per-request metadata')
                    value={'supportedVersions':MCP_VERSIONS,'capabilities':{'tools':{}},'instructions':'Discover contract and preflight; acquire one brand-scoped task at a time. HTTP /rpc is custom RPC, not MCP Streamable HTTP.'}
                elif method=='ping':value={}
                elif method=='tools/list':
                    if params.get('cursor') is not None:raise ProtocolError('Unknown tools cursor')
                    readonly={'negotiate','status','read_public_source','delivery_report','execution_metrics','cost_summary','trust_status','learning_status','commercial_rank','catalog','goal_summary','graph','memory_search','preflight','contract','execution_catalog','goal_readiness','capability_inventory','capability_readiness','experience_search','artifact_contract'}
                    value={'tools':[{'name':name,'description':tool_description(name),'inputSchema':self.schema(name),'annotations':{'readOnlyHint':name in readonly,'destructiveHint':False,'openWorldHint':False}} for name in sorted(self.methods)]}
                elif method=='tools/call':
                    name=params.get('name')
                    if name not in self.methods:raise ProtocolError('Unknown or unavailable tool')
                    arguments=params.get('arguments',{})
                    if not isinstance(arguments,dict):raise ProtocolError('Tool arguments must be an object')
                    try:
                        result=self.call(name,arguments)
                        value={'content':[{'type':'text','text':canonical(result)}],'structuredContent':result,'isError':False}
                    except (ValueError,KeyError,TypeError,WorkflowError) as exc:
                        from .files import redact
                        value={'content':[{'type':'text','text':str(exc)}],'isError':True,
                            'structuredContent':{'error':{'kind':getattr(exc,'kind','invalid_request'),'message':str(exc),'details':redact(getattr(exc,'details',{})),'retry_after':getattr(exc,'retry_after',None)}}}
                else:raise ProtocolError('Method not found',-32601)
            if modern:value={**value,'resultType':'complete','_meta':{PREFIX+'serverInfo':SERVER_INFO}}
            return {'jsonrpc':'2.0','id':ident,'result':value}
        except ProtocolError as exc:
            error={'code':exc.code,'message':str(exc)}
            if exc.data is not None:error['data']=exc.data
            return {'jsonrpc':'2.0','id':ident,'error':error}


def tool_description(name):
    descriptions={'contract':'Get model-neutral schemas, host requirements and work-cycle instructions.',
        'preflight':'Read-only diagnosis of missing tools, stale identity proof, account access and execution scope.',
        'acquire':'Lease one task matching this trusted worker; returns procedure and brand context. Does not grant platform permission.',
        'complete':'Validate a leased task receipt and advance its existing dependencies.',
        'action_prepare':'Reserve and authorize one external action before the host performs it.',
        'action_result':'Reconcile actual platform readback for the previously prepared operation.',
        'goal_summary':'Read the pinned goal, comparable observations, next work and actual waiting state.',
        'experiment_compare':'Compare preregistered business evidence with costs, uncertainty and quality gates; missing evidence waits.',
        'capability_discover':'Read actual schemas from an operator-bound tool process; this grants no platform access.',
        'plan_compose':'Compose known same-channel skills for an unstarted goal cycle, preserving actions and exact measurement.',
        'plan_apply':'Pin a composed plan before tasks exist in its cycle; never replace an executing graph.',
        'procedure_derive':'Generate a scoped procedural skill candidate from original recorded execution episodes.',
        'procedure_compare':'Read prospective independent execution episodes from storage; submitted success totals are not accepted.',
        'procedure_validate':'Verify a derived candidate on fresh assigned executions; compatibility is separate from sales success.'}
    return descriptions.get(name,'Brand-scoped growth operation: '+name.replace('_',' ')+'. Read the exported contract for inputs and limits.')


def stdio_loop(server, inp, out):
    for line in inp:
        try:
            message=json.loads(line,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Non-finite JSON')))
            reply=server.process(message)
        except (ValueError,TypeError):reply={'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Parse error'}}
        except Exception:reply={'jsonrpc':'2.0','id':message.get('id') if isinstance(message,dict) else None,'error':{'code':-32603,'message':'Internal error; progress is preserved'}}
        if reply is not None:out.write(canonical(reply)+'\n');out.flush()
