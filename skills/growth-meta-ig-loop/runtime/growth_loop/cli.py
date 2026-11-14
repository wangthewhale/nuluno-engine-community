import argparse
import json
import os
import sys
import time
from pathlib import Path

from .auth import TokenProvider, begin_oauth, receive_oauth
from .engine import Engine
from .bridge import record_browser_receipt
from .creative_agent import record_creative_receipt
from .errors import WorkflowError
from .files import atomic_json, read_json, redact
from .resources import skill_root
from .media import normalized_image, render_variant
from .policy import Policy, validate_profile
from .provider import MetaIGProvider
from .hybrid import HybridProvider
from .store import Store
from .transport import GraphClient
from .upload import AssetUploader
from .video import make_video
from .workflows import WorkflowRunner, record_workflow_receipt, validate_workflow_profile
from .actions import ActionManager, ActionAdapter
from .sales_state import SalesState
from .accounting import Ledger, roi_scenarios
from .contracts import blueprint
from .demand import search_plan, qualify, reply_brief, build_in_public_brief
from .inbound import ingest_signed, ingest_tool


def build_engine(store, run_id):
    initial = store.config(run_id)
    tokens = TokenProvider(store.root, initial.get("auth", {}).get("token_env", "META_ACCESS_TOKEN"))

    def factory(profile):
        graph = profile.get("graph", {})
        client = GraphClient(graph.get("base_url", "https://graph.facebook.com"), tokens,
                             graph.get("version"), profile.get("mode", "prepare"), graph.get("timeout_seconds", 20))
        auth = profile.get("auth", {})
        app_id = os.environ.get(auth.get("app_id_env", "META_APP_ID"))
        app_secret = os.environ.get(auth.get("app_secret_env", "META_APP_SECRET"))
        if auth.get("allow_existing_token_exchange") and app_id and app_secret:
            tokens.refresh_callback = lambda old: client.request("GET", "oauth/access_token", {
                "grant_type": "fb_exchange_token", "client_id": app_id,
                "client_secret": app_secret, "fb_exchange_token": old,
            }, authenticated=False)
        grant_path = store.root / "authorization.json"
        grant = read_json(grant_path) if grant_path.exists() else {}
        policy = Policy(profile, grant, store.clock)
        media = profile.get("media", {})
        uploader = AssetUploader(media["upload_url"], media.get("token_env"), profile.get("mode", "prepare")) if media.get("upload_url") else None
        provider = MetaIGProvider(client, profile, policy, store, uploader)
        return HybridProvider(provider, store, profile, policy)
    return Engine(store, factory, tokens)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Nuluno Engine Community preview: executable core, full S01 starter, separately licensed data-pack support. Default zero spend.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser('doctor')
    pack_install = sub.add_parser('method-pack-install', help='Validate and install a separately licensed data-only methods pack')
    pack_install.add_argument('--archive', required=True)
    pack_install.add_argument('--directory', required=True)
    pack_install.add_argument('--sha256', required=True)
    sub.add_parser('method-pack-status')
    standalone = sub.add_parser('init-service')
    standalone.add_argument('--workspace', required=True)
    standalone.add_argument('--config', required=True)
    standalone.add_argument('--brand-url', required=True)
    standalone.add_argument('--brand-name')
    standalone.add_argument('--run-id', required=True)
    standalone.add_argument('--worker', default='external')
    standalone.add_argument('--token-env', default='ENGINE_EXTERNAL_AGENT_TOKEN')
    evolution = sub.add_parser('evolution')
    evolution.add_argument('--workspace', required=True)
    evolution.add_argument('--config', required=True)
    evolution.add_argument('--worker', required=True)
    evolution.add_argument('--run-id', required=True)
    snapshot = sub.add_parser('export-snapshot')
    snapshot.add_argument('--workspace', required=True)
    snapshot.add_argument('--output', required=True)
    restore = sub.add_parser('restore-snapshot')
    restore.add_argument('--input', required=True)
    restore.add_argument('--workspace', required=True)
    init = sub.add_parser("init")
    init.add_argument("--profile", required=True)
    init.add_argument("--workspace", required=True)
    init.add_argument("--run-id")
    sub.add_parser("workflows")
    sub.add_parser('goals')
    sub.add_parser('channel-skills')
    sub.add_parser('contract')
    sub.add_parser('coverage')
    sub.add_parser('execution-catalog')
    readiness=sub.add_parser('goal-readiness')
    readiness.add_argument('--profile',required=True)
    readiness.add_argument('--config',required=True)
    readiness.add_argument('--worker',default='host')
    readiness.add_argument('--goal-id')
    preflight=sub.add_parser('preflight')
    preflight.add_argument('--workspace',required=True)
    preflight.add_argument('--run-id',required=True)
    preflight.add_argument('--inventory')
    preflight.add_argument('--config')
    preflight.add_argument('--worker',default='host')
    connect=sub.add_parser('connection-info')
    connect.add_argument('--workspace',required=True)
    connect.add_argument('--config',required=True)
    connect.add_argument('--worker',default='host')
    for name in ('goal-init','channel-init'):
        item=sub.add_parser(name)
        item.add_argument('--profile',required=True)
        item.add_argument('--workspace',required=True)
        item.add_argument('--run-id')
        item.add_argument('--goal-id' if name=='goal-init' else '--channel-skill',required=True)
    for name in ("workflow-init", "skill-init"):
        item = sub.add_parser(name)
        item.add_argument("--profile", required=True)
        item.add_argument("--workspace", required=True)
        item.add_argument("--run-id")
        if name == "skill-init": item.add_argument("--skill-id", required=True)
    for name in ("tick", "status", "watch", "resume", "report"):
        command = sub.add_parser(name)
        command.add_argument("--workspace", required=True)
        command.add_argument("--run-id", required=True)
        if name == "resume":
            command.add_argument("--profile", help="完整且已確認的新設定；不會自動產生授權")
        if name == "watch":
            command.add_argument("--interval", type=float, default=15)
            command.add_argument("--max-ticks", type=int, default=0)
        if name == "report":
            command.add_argument("--output", required=True)
    render = sub.add_parser("render")
    render.add_argument("--profile", required=True)
    render.add_argument("--cycle", type=int, default=1)
    render.add_argument("--output", required=True)
    normalize = sub.add_parser("normalize-image")
    normalize.add_argument("--input", required=True)
    normalize.add_argument("--output", required=True)
    video = sub.add_parser("video")
    video.add_argument("--input", required=True)
    video.add_argument("--output", required=True)
    video.add_argument("--duration", type=int, default=6)
    auth = sub.add_parser("auth-link")
    auth.add_argument("--app-id", required=True)
    auth.add_argument("--redirect-uri", required=True)
    auth.add_argument("--state-file", required=True)
    auth.add_argument("--scopes", required=True)
    login = sub.add_parser("auth-login")
    login.add_argument("--profile", required=True)
    login.add_argument("--workspace", required=True)
    login.add_argument("--port", type=int, default=8765)
    for name in ("browser-receipt", "creative-receipt", "workflow-receipt"):
        receipt = sub.add_parser(name)
        receipt.add_argument("--workspace", required=True)
        receipt.add_argument("--request", required=True)
        receipt.add_argument("--result", required=True)
    for name in ("action-prepare", "action-execute"):
        action = sub.add_parser(name)
        action.add_argument("--workspace", required=True)
        action.add_argument("--request", required=True)
        action.add_argument("--action", required=True)
    action_result = sub.add_parser("action-result")
    action_result.add_argument("--workspace", required=True)
    action_result.add_argument("--run-id", required=True)
    action_result.add_argument("--operation-key", required=True)
    action_result.add_argument("--result", required=True)
    for name in ("contacts", "followups", "ledger-import", "ledger-summary", "event-ingest", "event-record"):
        item = sub.add_parser(name)
        item.add_argument("--workspace", required=True)
        item.add_argument("--run-id", required=True)
        if name in ("contacts", "ledger-import"): item.add_argument("--input", required=name == "ledger-import")
        if name == "ledger-summary":
            item.add_argument("--currency", required=True)
            item.add_argument("--owner-share")
            item.add_argument("--reserves-complete", action="store_true")
        if name == "event-ingest":
            item.add_argument("--body", required=True)
            item.add_argument("--headers", required=True)
            item.add_argument("--source", required=True)
        if name == "event-record":
            item.add_argument("--input", required=True)
            item.add_argument("--receipt", required=True)
            item.add_argument("--source", required=True)
    for name in ("demand-plan", "demand-qualify", "story-plan"):
        item = sub.add_parser(name)
        item.add_argument("--profile", required=True)
        item.add_argument("--output", required=True)
        if name in ("demand-qualify", "story-plan"): item.add_argument("--input", required=True)
    roi = sub.add_parser("roi")
    roi.add_argument("--input", required=True)
    for name in ('serve', 'mcp', 'rpc'):
        item = sub.add_parser(name)
        item.add_argument('--workspace', required=True)
        item.add_argument('--config', required=True)
        if name != 'serve': item.add_argument('--worker', required=True)
        if name == 'serve':
            item.add_argument('--host', default='127.0.0.1')
            item.add_argument('--port', type=int, default=8766)
        if name == 'rpc':
            item.add_argument('--method', required=True)
            item.add_argument('--input', required=True)
    worker = sub.add_parser('worker')
    worker.add_argument('--url', required=True)
    worker.add_argument('--worker', required=True)
    worker.add_argument('--token-env', required=True)
    worker.add_argument('--run-id', required=True)
    worker.add_argument('--interval', type=float, default=15)
    worker.add_argument('--max-polls', type=int, default=0)
    worker.add_argument('host_command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == 'method-pack-install':
        from .method_packs import install_pack
        from .resources import bundle_root
        try:
            result = install_pack(args.archive, args.directory, args.sha256, bundle_root())
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        except (ValueError, OSError, KeyError) as exc:
            print(json.dumps({'installed':False,'error':str(exc) if isinstance(exc,ValueError) else 'pack_install_failed'})); return 2
    if args.command == 'method-pack-status':
        from .resources import library_status
        print(json.dumps(library_status(), ensure_ascii=False, indent=2)); return 0
    if args.command == 'init-service':
        from .standalone import initialize_service
        result = initialize_service(args.workspace, args.config, args.brand_url, args.run_id, args.worker, args.token_env, args.brand_name)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == 'doctor':
        from . import __version__
        from .resources import library_status
        from .service import METHODS
        from .media import dependency_status
        folders = [p for p in skill_root().parent.iterdir() if p.is_dir() and (p / 'SKILL.md').is_file() and (p / '.growth-pack.json').is_file()]
        result = {'product': 'Nuluno Engine Community', 'version': __version__, **library_status(),
                  'methods': len(METHODS), 'resources': str(skill_root()), 'evolution_controller': True,
                  'model_configured': False, 'background_worker_running_inferred': False, 'all_platforms_ready_inferred': False,
                  'optional_media': dependency_status()}
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command in ('export-snapshot', 'restore-snapshot'):
        from .portability import export_snapshot, restore_snapshot
        result = export_snapshot(args.workspace, args.output) if args.command == 'export-snapshot' else restore_snapshot(args.input, args.workspace)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command == 'evolution':
        from .service import Service, load_worker
        local_store = Store(args.workspace)
        try:
            result = Service(local_store, args.worker, load_worker(read_json(args.config), args.worker)).run('evolution_tick', {'run_id': args.run_id})
        finally:
            local_store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    if args.command in ('serve', 'mcp', 'rpc'):
        from .service import Service, serve, mcp_stdio, load_worker
        config = read_json(args.config)
        if args.command == 'serve': return serve(args.workspace, config, args.host, args.port)
        if args.command == 'mcp': return mcp_stdio(args.workspace, config, args.worker)
        local_store = Store(args.workspace)
        try: result = Service(local_store, args.worker, load_worker(config, args.worker)).run(args.method, read_json(args.input))
        finally: local_store.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == 'worker':
        from .worker import work
        command = args.host_command
        if command and command[0] == '--': command = command[1:]
        if not command: parser.error('需要可執行的宿主Agent指令')
        return work(args.url, args.worker, args.token_env, args.run_id, command, args.interval, args.max_polls)

    store = None
    try:
        if args.command=='contract':
            from .onboarding import host_contract
            result=host_contract()
        elif args.command=='connection-info':
            from .service import load_worker
            from .mcp_protocol import MCP_VERSIONS
            settings=load_worker(read_json(args.config),args.worker)
            result={'mcpServers':{'growth':{'command':sys.executable,'args':['-m',__package__+'.cli','mcp','--workspace',str(Path(args.workspace).resolve()),'--config',str(Path(args.config).resolve()),'--worker',args.worker]}},'supported_mcp_versions':MCP_VERSIONS,'allowed_runs':settings.get('runs',[]),'agent_configuration_changed':False,'next':'Add this local server using the host MCP settings; then contract, run_create if allowed, preflight, acquire.'}
        elif args.command=='coverage':
            result=read_json(skill_root()/'references/coverage-catalog.json')
        elif args.command=='execution-catalog':
            from .execution import execution_catalog
            result=execution_catalog()
        elif args.command=='goal-readiness':
            from .execution import readiness
            config=read_json(args.config)
            result=readiness(read_json(args.profile),config.get('workers',{}).get(args.worker,{}),selected_goal=args.goal_id)
        elif args.command=='preflight':
            from .onboarding import preflight as inspect_readiness
            settings=read_json(args.config).get('workers',{}).get(args.worker,{}) if args.config else {}
            inventory=read_json(args.inventory) if args.inventory else settings.get('tool_inventory',[])
            caps=settings.get('capabilities',sorted({c for route in inventory for c in route.get('capabilities',[])}))
            store=Store(args.workspace)
            result=inspect_readiness(store,args.run_id,caps,inventory,settings.get('verification_max_age',3600))
        elif args.command in ('goals','channel-skills'):
            if args.command=='goals':
                from .goals import goal_catalog
                result=goal_catalog()
            else:
                from .channels import channel_catalog
                result=channel_catalog()
        elif args.command in ('goal-init','channel-init'):
            profile=read_json(args.profile)
            if args.command=='goal-init':
                profile['workflow']={**profile.get('workflow',{}),'id':'goal'}
                profile['goal']={**profile.get('goal',{}),'id':args.goal_id}
                if args.goal_id in ('G065', 'G066'):
                    profile['acquisition']={**profile.get('acquisition',{}),'raised_hand_only':True}
            else:
                from .channels import channel_skill
                spec=channel_skill(args.channel_skill)
                profile['workflow']={**profile.get('workflow',{}),'id':'single','skill_id':spec['parent_skill_id'],'channel_skill_id':spec['id']}
            validate_workflow_profile(profile)
            store=Store(args.workspace);run_id=store.create_run(profile,args.run_id);WorkflowRunner(store).seed(run_id)
            result={'run_id':run_id,'mode':profile.get('mode','prepare'),'workflow':profile['workflow'],'external_actions':False}
        elif args.command == "workflows":
            result = {"workflows": [{"id": f["id"], "name": f["name"], "stages": len(f["stages"])} for f in blueprint()["workflows"]], "skill_count": len(blueprint()["skills"]), "stages": sum(len(f["stages"]) for f in blueprint()["workflows"])}
        elif args.command in ("workflow-init", "skill-init"):
            profile = read_json(args.profile)
            if args.command == "skill-init":
                if args.skill_id == "S35":
                    profile["workflow"] = {"id": "M08", "method_skill_id": "S35", "max_iterations": 1}
                    profile.setdefault("acquisition", {})["raised_hand_only"] = True
                else:
                    profile["workflow"] = {"id": "single", "skill_id": args.skill_id, "max_iterations": 1}
            profile = validate_workflow_profile(profile)
            store = Store(args.workspace)
            run_id = store.create_run(profile, args.run_id)
            WorkflowRunner(store).seed(run_id)
            result = {"run_id": run_id, "mode": profile.get("mode", "prepare"), "workflow": profile["workflow"], "external_actions": False}
        elif args.command == "init":
            profile = validate_profile(read_json(args.profile))
            store = Store(args.workspace)
            run_id = store.create_run(profile, args.run_id)
            build_engine(store, run_id).seed(run_id)
            result = {"run_id": run_id, "mode": profile.get("mode", "prepare"), "publication": False}
        elif args.command == "render":
            result = render_variant(validate_profile(read_json(args.profile)), args.cycle, args.output)
        elif args.command == "normalize-image":
            result = normalized_image(args.input, args.output)
        elif args.command == "video":
            result = make_video(args.input, args.output, args.duration)
        elif args.command == "auth-link":
            result = {"authorization_url": begin_oauth(args.app_id, args.redirect_uri, args.scopes.split(","), args.state_file),
                      "next": "使用官方登入；回呼接收與交換尚需配置，不要貼密碼或驗證碼到聊天。"}
        elif args.command == "auth-login":
            result = receive_oauth(validate_profile(read_json(args.profile)), args.workspace, args.port)
        elif args.command == "browser-receipt":
            store = Store(args.workspace)
            result = record_browser_receipt(store, read_json(args.request), read_json(args.result))
        elif args.command == "creative-receipt":
            store = Store(args.workspace)
            result = record_creative_receipt(store, read_json(args.request), read_json(args.result))
        elif args.command == "workflow-receipt":
            store = Store(args.workspace)
            result = record_workflow_receipt(store, read_json(args.request), read_json(args.result))
        elif args.command in ("action-prepare", "action-execute"):
            store = Store(args.workspace)
            request, action = read_json(args.request), read_json(args.action)
            manager = ActionManager(store)
            result = manager.prepare(request, action) if args.command == "action-prepare" else ActionAdapter(store.config(request["run_id"])).execute(manager, request, action)
        elif args.command == "action-result":
            store = Store(args.workspace)
            result = ActionManager(store).result(args.run_id, args.operation_key, read_json(args.result))
        elif args.command in ("contacts", "followups", "ledger-import", "ledger-summary", "event-ingest", "event-record"):
            store = Store(args.workspace)
            brand = store.config(args.run_id)["brand"]["url"]
            if args.command == "contacts":
                sales = SalesState(store)
                result = [sales.upsert_contact(brand, row) for row in read_json(args.input)] if args.input else sales.contacts(brand)
            elif args.command == "followups": result = SalesState(store).due_followups(brand)
            elif args.command == "ledger-import": result = [Ledger(store).add(brand, row) for row in read_json(args.input)]
            elif args.command == "ledger-summary": result = Ledger(store).summary(brand, args.currency, owner_share=args.owner_share, reserve_complete=args.reserves_complete)
            elif args.command == "event-record": result = ingest_tool(store, args.run_id, read_json(args.input), read_json(args.receipt), args.source)
            else: result = ingest_signed(store, args.run_id, Path(args.body).read_bytes(), read_json(args.headers), args.source)
        elif args.command in ("demand-plan", "demand-qualify", "story-plan"):
            profile = read_json(args.profile)
            acquisition = profile.get("acquisition", {})
            if args.command == "demand-plan": result = search_plan(profile["brand"], acquisition.get("buyer_terms", []), acquisition.get("competitors", []), acquisition.get("communities", []))
            elif args.command == "demand-qualify":
                result = qualify(read_json(args.input), profile["brand"], time.time, acquisition.get("max_age_days", 14))
                result["reply_briefs"] = [reply_brief(row, profile["brand"]) for row in result["qualified"]]
            else:
                source = read_json(args.input)
                result = build_in_public_brief(profile["brand"], source.get("verified_facts", []), source.get("lessons", []))
            atomic_json(args.output, result)
        elif args.command == "roi": result = roi_scenarios(read_json(args.input))
        else:
            store = Store(args.workspace)
            if args.command == "status":
                result = store.status(args.run_id)
            elif args.command == "resume":
                if args.profile:
                    incoming = read_json(args.profile)
                    profile = validate_workflow_profile(incoming) if incoming.get("workflow") else validate_profile(incoming)
                    old = store.config(args.run_id)
                    if old["brand"]["url"] != profile["brand"]["url"]:
                        raise ValueError("Cannot replace a run with another brand")
                    store.update_config(args.run_id, profile)
                store.resume_waiting(args.run_id)
                result = store.status(args.run_id)
            elif args.command == "report":
                from .report import write_report
                result = write_report(store, args.run_id, args.output)
            elif args.command == "tick":
                result = build_engine(store, args.run_id).run_tick(args.run_id)
            elif args.command == "watch":
                if not 1 <= args.interval <= 60:
                    raise ValueError("watch interval must be 1 to 60 seconds")
                n = 0
                while True:
                    result = build_engine(store, args.run_id).run_tick(args.run_id)
                    n += 1
                    if args.max_ticks and n >= args.max_ticks:
                        break
                    time.sleep(args.interval)
        print(json.dumps(redact(result), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except WorkflowError as exc:
        print(json.dumps(redact({"status": exc.kind, "reason": str(exc), "details": exc.details}), ensure_ascii=False))
        return 2
    except (ValueError, KeyError, OSError) as exc:
        print(json.dumps(redact({"status": "invalid_input", "reason": str(exc)}), ensure_ascii=False))
        return 2
    finally:
        if store:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
