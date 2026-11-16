"""Read-only host discovery and brand-scoped execution readiness diagnostics."""
import math
from .channels import channel_catalog, channel_skill
from .contracts import CAPABILITIES, ALLOWED_ACTIONS, _proof, blueprint
from .files import read_json
from .errors import Waiting, WorkflowError
from .resources import library_status, bundle_root, reference

VERSION='0.9.1'


def host_contract():
    from .service import METHODS, input_schema, MCP_VERSIONS
    from . import __version__
    # The genuine community wheel ships S01, not a fake empty paid catalog.
    # Absence is valid only when the distribution manifest explicitly describes
    # the installed starter. An active licensed library missing its catalog is
    # an integrity/configuration error and must not be downgraded to a starter.
    status=library_status()
    installed_ids={skill['id'] for skill in blueprint()['skills']}
    if any(ident not in CAPABILITIES for ident in installed_ids):
        raise ValueError('installed_skill_capability_contract_missing')
    catalog_path=reference('channel-catalog.json')
    if catalog_path.is_file():
        catalog=read_json(catalog_path)
        channel_skills=catalog['channel_skills']
    else:
        manifest_path=bundle_root()/'community.json'
        manifest=read_json(manifest_path) if manifest_path.is_file() else {}
        if status['active_pack'] is not None or manifest.get('schema')!='nuluno-community-distribution-v1' or manifest.get('full_methods_pack_included') is not False or not isinstance(manifest.get('starter_skill_ids'),list) or set(manifest['starter_skill_ids'])!=installed_ids:
            raise ValueError('installed_channel_catalog_missing')
        channel_skills=[]
    capabilities=sorted({c for ident in installed_ids for c in CAPABILITIES[ident]}|{s['capability'] for s in channel_skills})
    return {'schema':'growth-host-contract-v1','version':VERSION,'engine_version':__version__,'product':'Nuluno Engine','model_neutral':True,
        'autonomous_evolution':{'controller':'nuluno-evolution-1','configure':'operator config: allow_evolution and evolution.enabled',
            'execution':'existing acquire cycle or evolution_tick; no second scheduler',
            'proposal':'bounded model adapter; original episodes and allowed data-only read transformations',
            'promotion':'fresh assigned canary and independent fixed-horizon comparison',
            'rollback':'new fixed monitoring windows; original requests unchanged',
            'weights_updated':False,'business_success_inferred':False},
        'interfaces':{'skill_files':'host reads SKILL.md and can execute local commands','cli':'JSON stdout; nonzero exit is failure','mcp_stdio':{'versions':MCP_VERSIONS,'transport':'newline JSON-RPC over local stdio'},'mcp_streamable_http':{'path':'/mcp','adapter':'deploy/mcp-gateway/server.mjs','authentication':'independent operator-issued worker Bearer','nuluno_com_required':False},'http_rpc':{'path':'/rpc','authentication':'Bearer token and X-Growth-Worker; custom RPC, not Streamable HTTP MCP'}},
        'capabilities':capabilities,'methods':{name:input_schema(name) for name in METHODS},
        'library_inventory':{'edition':status['edition'],'installed_work_skill_ids':sorted(installed_ids),
            'active_data_pack_installed':status['active_pack'] is not None,'channel_catalog_installed':catalog_path.is_file(),
            'installed_channel_skill_count':len(channel_skills),'platform_execution_skills_included':bool(channel_skills),
            'goal_library_installed':status['goal_library_installed'],'goals':status['goals'],'graph_templates':status['graph_templates'],
            'credentials_or_authority_provided':False},
        'work_cycle':['tool_probe for operator-bound tools','preflight','acquire','read request/context and procedure','heartbeat while working','tool_execute (write-ahead plus native readback) or scoped host action_prepare/action_result','complete with structured receipt'],
        'executable_tool_dispatch':{'mcp_stdio_client':True,'public_source_reader':True,'registered_routes_required':True,'not_all_platform_sdks':True},
        'source_ingress':{'signed_observations':True,'stripe_original_body_signature':True,'automatic_source_provisioning':False},
        'adaptive_execution':{'capability_schemas':'capability_discover then tool_probe; same operator-bound adapter and account',
            'composition':'plan_compose then plan_apply, only before tasks exist in that cycle; run_create may include proposal',
            'candidate_generation':'actual tool traces -> immutable execution episodes -> procedure_derive',
            'candidate_verification':'prospective assigned new tasks -> procedure_validate -> procedure_compare -> procedure_adopt or retain',
            'learning_families':{'execution':'tool-task success, original episodes, independent tasks, uncertainty and latency guardrail',
                'goal':'exact original nonfinancial goal, prospective run groups, fixed windows and metric bounds',
                'economic':'net per customer with original money, refunds, reserves, eight cost categories and uncertainty'},
            'permissions_mutable':False,'model_proposals_are_not_executable_code':True,'self_modifying_kernel':False},
        'inventory_fields':{'tool_id':'host tool name','capabilities':'supported capability IDs','channel':'channel identifier, web or local','account_ref':'exact account for channel operations','actions':'permitted action names supported by tool','verified_at':'UTC epoch of real identity/capability read','evidence':'list of source references from trusted host readback'},
        'trust':'Inventory and receipts are authenticated trusted-host attestations; not independent verification of platform claims.',
        'readiness_states':['planning_ready','tools_missing','account_pending','scope_pending','ready_for_scoped_execution'],
        'continuous_execution':'Requires a running trusted host worker and platform tools; skill installation alone does not run one.',
        'universal_agent_certification':False}


def requirements(profile, tasks=None):
    from .goals import compile_goal,goal_definition
    if tasks is None:
        workflow=profile.get('workflow',{})
        if workflow.get('id')=='goal':
            nodes=compile_goal(goal_definition(profile['goal']['id']),profile['goal'])['nodes']
            tasks=[{'data':{'skill_id':channel_skill(n['skill'])['parent_skill_id'] if not n['skill'].startswith('S') else n['skill'],'channel_skill_id':n['skill'] if not n['skill'].startswith('S') else None}} for n in nodes if n.get('skill')]
        elif workflow.get('channel_skill_id'):
            spec=channel_skill(workflow['channel_skill_id']);tasks=[{'data':{'skill_id':spec['parent_skill_id'],'channel_skill_id':spec['id']}}]
        elif workflow.get('id')=='single':tasks=[{'data':{'skill_id':workflow['skill_id']}}]
        else:
            from .contracts import blueprint
            flow=next(f for f in blueprint()['workflows'] if f['id']==workflow['id'])
            tasks=[{'data':{'skill_id':ident}} for stage in flow['stages'] for ident in stage['skill_ids']]
    result=[];seen=set()
    for task in tasks:
        data=task['data'];ident=data.get('skill_id')
        if not ident:continue
        spec=channel_skill(data['channel_skill_id']) if data.get('channel_skill_id') else None
        from .execution import required_capabilities
        caps=required_capabilities(ident,profile,data.get('closed_empty_query',False))+([spec['capability']] if spec else [])
        actions=spec['allowed_actions'] if spec and ident!='S05' else []
        for cap in caps:
            key=(cap,spec['channel'] if spec else None,tuple(actions))
            if key not in seen:
                result.append({'capability':cap,'channel':key[1],'actions':list(actions),'skill_id':spec['id'] if spec else ident,'access_only':ident=='S05'});seen.add(key)
    return result


def route_matches(route, requirement, profile, clock, max_age):
    if not isinstance(route,dict) or requirement['capability'] not in route.get('capabilities',[]):return False
    channel=requirement['channel']
    if channel and route.get('channel')!=channel:return False
    if not set(requirement['actions']).issubset(set(route.get('actions',[]))):return False
    stamp=route.get('verified_at');proof=route.get('evidence')
    if type(stamp) not in (int,float) or not math.isfinite(stamp) or not clock-max_age<=stamp<=clock+300:return False
    if not route.get('tool_id') or not isinstance(proof,list) or not proof or any(not isinstance(p,dict) or not _proof(p.get('reference'),profile.get('mode','prepare')) for p in proof):return False
    if channel and channel!='audience' and not requirement.get('access_only'):
        account=profile.get('bindings',{}).get(channel,{}).get('account_ref')
        if not account or route.get('account_ref')!=account:return False
    return True


def preflight(store, run_id, capabilities=(), inventory=None, max_age=3600, tasks=None):
    if type(max_age) not in (int,float) or not math.isfinite(max_age) or not 0<max_age<=86400:raise ValueError('工具驗證有效期需為0至86400秒內正數')
    if inventory is None:inventory=[]
    if not isinstance(inventory,list):raise ValueError('inventory需工具清單')
    profile=store.config(run_id)
    if tasks is not None and profile.get('workflow',{}).get('id')=='goal':
        tasks=[{**task,'data':{**task['data'],'closed_empty_query':bool(store.artifact(run_id,task.get('cycle',1),'goal.empty_branch.'+task['data'].get('branch','')))}} for task in tasks]
    needed=requirements(profile,tasks);declared=set(capabilities)
    unknown_declared=sorted(declared-set(host_contract()['capabilities']))
    missing=[];unverified=[];channels={};scope=[]
    connected=store.artifact(run_id,0,'connected_accounts') or {'accounts':[]}
    grant_file=store.root/'authorization.json';grant=read_json(grant_file) if grant_file.exists() else {}
    from .policy import Policy
    for req in needed:
        cap=req['capability'];channel=req['channel']
        if cap not in declared:missing.append(req)
        elif not (cap.startswith(('local.','agent.')) or route_matches_any(inventory,req,profile,store.clock(),max_age)):unverified.append(req)
        if channel and channel!='audience':
            account=profile.get('bindings',{}).get(channel,{}).get('account_ref')
            verified=any(a.get('channel')==channel and a.get('account_ref')==account and a.get('state')=='connected' for a in connected.get('accounts',[]))
            channels[channel]={'account_ref':account,'connected_readback':verified,'resume_url':channel_catalog()['channels'][channel]['entry_url']}
            if req['actions']:
                # Alternatives: a reply-only tool may satisfy a send/reply skill.
                permitted=False
                for action in req['actions']:
                    try:
                        Policy(profile,grant,store.clock).require(action,account,public=action in ('publish_content','update_content','publish_listing','update_listing'),spending=action=='activate_ads')
                        permitted=True;break
                    except (Waiting,WorkflowError,ValueError,TypeError):pass
                if not permitted:scope.append({'channel':channel,'skill_id':req['skill_id'],'actions':req['actions'],'reason':'no currently valid scoped action alternative'})
    account_pending=any(not row['account_ref'] or not row['connected_readback'] for row in channels.values())
    source_missing=bool(missing or unverified)
    state='tools_missing' if source_missing else 'account_pending' if account_pending else 'scope_pending' if scope else 'ready_for_scoped_execution'
    if profile.get('mode','prepare')=='prepare' and not source_missing:state='planning_ready'
    if profile.get('example_only') and profile.get('mode')=='live':state='scope_pending';scope.append({'reason':'example profile cannot execute a real brand'})
    return {'run_id':run_id,'brand_url':profile['brand']['url'],'state':state,'ready_for_execution':state=='ready_for_scoped_execution','read_only':True,
        'required':needed,'missing_capabilities':missing,'unverified_or_stale_tools':unverified,'unknown_declared_capabilities':unknown_declared,'accounts':channels,'scope_pending':scope,
        'can_continue_research':all(c in declared for c in ('web.read','web.research')),
        'can_continue_public_reads':'web.read' in declared and not any(r['capability']=='web.read' for r in missing+unverified),
        'next_actions':[{'kind':'connect_or_verify_tool','capability':r['capability'],'channel':r['channel']} for r in missing+unverified]+[{'kind':'normal_account_access','channel':ch,**v} for ch,v in channels.items() if not v['connected_readback']]+[{'kind':'apply_existing_scope_or_request_missing_scope',**s} for s in scope],
        'trust':'trusted-host evidence; not independently verified platform capability','market_success_verified':False}


def route_matches_any(inventory,req,profile,clock,max_age):
    # Actions are alternatives, not an obligation to implement every alternative.
    if req['actions']:
        return any(route_matches(route,{**req,'actions':[action]},profile,clock,max_age) for action in req['actions'] for route in inventory)
    return any(route_matches(route,req,profile,clock,max_age) for route in inventory)
