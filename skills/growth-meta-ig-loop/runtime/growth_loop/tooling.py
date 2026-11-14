"""Execute operator-bound tools; a procedure/capability name is not a tool.

The operator configuration is trusted code-equivalent configuration. Tool text,
website content, model output and a workflow receipt cannot register routes.
"""
import ipaddress
import json
import math
import os
import queue
import re
import socket
import subprocess
import threading
import time
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse,urljoin
from urllib.request import Request, build_opener

from .errors import Waiting, WorkflowError, UncertainWrite
from .files import canonical, digest, redact
from .transport import NoRedirect

MAX_BYTES = 2 * 1024 * 1024


def pointer(value, path):
    """JSON Pointer, never Python/JavaScript expressions."""
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("Result mappings require a JSON Pointer")
    for part in path[1:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def bind(template, context):
    if isinstance(template, dict):
        if set(template) == {"$pointer"}:
            return pointer(context, template["$pointer"])
        return {k: bind(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [bind(v, context) for v in template]
    return template


def structured(result):
    if not isinstance(result, dict) or result.get("isError"):
        detail=result.get('structuredContent',{}) if isinstance(result,dict) else {}
        if not detail and isinstance(result,dict):detail={'provider_message':[c.get('text','')[:2000] for c in result.get('content',[]) if c.get('type')=='text'][:2]}
        raise WorkflowError("連接工具回報失敗；保留原因供原任務續接，未取得平台成果。", "tool_rejected",details={'native_error':redact(detail)})
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    if len(texts) == 1:
        try:
            value = json.loads(texts[0])
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    raise WorkflowError("此工具沒有可解析的結構化結果；需完成專用轉接，不能猜欄位。", "waiting_capability")


class MCPStdioClient:
    """Bounded MCP client for operator-owned stdio servers. No shell/download."""
    def __init__(self, config):
        command = config.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(x, str) for x in command):
            raise ValueError("MCP command must be an operator-owned argument array")
        timeout = config.get("timeout_seconds", 30)
        if type(timeout) not in (int, float) or not 0 < timeout <= 120:
            raise ValueError("Tool timeout must be within 120 seconds")
        self.timeout, self.ident = timeout, 0
        self.deadline=time.monotonic()+timeout
        names = {"PATH", "HOME", "SYSTEMROOT", "LANG", "TMPDIR"} | set(config.get("env_allow", []))
        env = {k: os.environ[k] for k in names if k in os.environ}
        self.child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, env=env, shell=False)
        self.messages = queue.Queue(maxsize=128)
        def read():
            try:
                while True:
                    line = self.child.stdout.readline(MAX_BYTES + 1)
                    if not line:
                        self.messages.put(EOFError()); return
                    if len(line) > MAX_BYTES:
                        self.messages.put(ValueError("MCP response too large")); return
                    self.messages.put(json.loads(line))
            except Exception as exc:
                self.messages.put(exc)
        threading.Thread(target=read, daemon=True).start()
        try:
            hello = self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                 "clientInfo": {"name": "growth-tool-client", "version": "0.7.0"}})
            if hello.get("protocolVersion") not in ("2025-11-25", "2025-06-18"):
                raise Waiting("工具伺服器需要尚未驗收的MCP協定版本。", "waiting_capability")
            self.notify("notifications/initialized", {})
        except Exception:
            self.close(); raise

    def notify(self, method, params):
        self.child.stdin.write((canonical({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode())
        self.child.stdin.flush()

    def request(self, method, params):
        self.ident += 1
        ident = self.ident
        self.child.stdin.write((canonical({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}) + "\n").encode())
        self.child.stdin.flush()
        deadline = min(self.deadline,time.monotonic() + self.timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("MCP tool response not confirmed")
            try:
                message = self.messages.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("MCP tool response not confirmed")
            if isinstance(message, Exception):
                raise message
            if "method" in message:
                if "id" in message:
                    # Sampling, authentication and elicitation cannot acquire
                    # authority from a provider-initiated request.
                    reply = {"jsonrpc": "2.0", "id": message["id"], "error": {
                        "code": -32601, "message": "Requires an explicit host/human capability"}}
                    self.child.stdin.write((canonical(reply) + "\n").encode()); self.child.stdin.flush()
                continue
            if message.get("id") != ident:
                continue
            if "error" in message:
                error=message['error']
                if isinstance(error,dict) and error.get('code')==-32603:
                    raise WorkflowError('MCP伺服器內部失敗；保留原任務供續接。','tool_error',details={'rpc_error':redact(error)})
                raise WorkflowError("MCP工具請求遭明確拒絕。", "tool_rejected",details={'rpc_error':redact(error)})
            return message["result"]

    def tools(self):
        found, cursor, seen = [], None, set()
        for _ in range(50):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            found.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return found
            if cursor in seen:
                raise ValueError("Repeated MCP tool-list cursor")
            seen.add(cursor)
        raise ValueError("MCP tool list exceeded bounded pagination")

    def call(self, name, arguments, before_dispatch=None):
        # Discovery is evidence of the actual schema, not write permission.
        spec = next((x for x in self.tools() if x.get("name") == name), None)
        if spec is None:
            raise Waiting("設定的工具名稱不在目前伺服器清單。", "waiting_capability")
        schema = spec.get("inputSchema", {})
        if not isinstance(arguments, dict) or any(k not in arguments for k in schema.get("required", [])):
            raise ValueError("Native tool arguments do not satisfy required fields")
        if schema.get("additionalProperties") is False and set(arguments) - set(schema.get("properties", {})):
            raise ValueError("Unexpected native tool arguments")
        if before_dispatch:before_dispatch()
        return structured(self.request("tools/call", {"name": name, "arguments": arguments}))

    def close(self):
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.child.kill(); self.child.wait()
        for stream in (self.child.stdin, self.child.stdout):
            if stream:
                try:stream.close()
                except OSError:pass


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.hidden = max(0, self.hidden - 1)
    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def fetch_public(url, allowed_hosts, fixture=False, redirects=()):
    parsed = urlparse(url)
    if parsed.scheme not in (("http", "https") if fixture else ("https",)) or parsed.username or parsed.password or parsed.fragment or parsed.hostname not in allowed_hosts:
        raise WorkflowError("來源不在這個工具已設定的公開HTTPS主機範圍。", "scope_mismatch")
    addresses = {x[4][0] for x in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    if not fixture and any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise WorkflowError("公開來源工具不讀取私人網路。", "scope_mismatch")
    request = Request(url, headers={"User-Agent": "GrowthSourceReader/0.7.0", "Accept": "text/html,application/json,text/plain,text/markdown,application/markdown"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("Public source exceeded size limit")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if content_type not in ("text/html", "application/json", "text/plain", "application/xhtml+xml", "text/markdown", "application/markdown"):
                raise WorkflowError("公開來源不是支援的文字／JSON格式。",'unsupported_content_type',details={'url':url,'content_type':content_type,'bytes':len(raw),'body_sha256':digest(raw)})
            text = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
            if content_type in ("text/html", "application/xhtml+xml"):
                parser = _Text(); parser.feed(text); text = "\n".join(parser.parts)
            return {"url": url, "requested_url":redirects[0]['from'] if redirects else url,
                    "redirect_chain":list(redirects),"status": response.status, "content_type": content_type,
                    "text": text[:40000],"text_truncated":len(text)>40000,"bytes":len(raw), "body_sha256": digest(raw), "observed_at": time.time()}
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            target=urljoin(url,exc.headers.get('Location',''))
            dest=urlparse(target)
            port=lambda p:p.port or (443 if p.scheme=='https' else 80)
            if target and dest.hostname==parsed.hostname and port(dest)==port(parsed) and dest.scheme==parsed.scheme and not dest.username and not dest.password and not dest.fragment and len(redirects)<5:
                return fetch_public(target,allowed_hosts,fixture,tuple(redirects)+({'status':exc.code,'from':url,'to':target},))
            raise Waiting("來源跨主機／連接埠轉址或循環需先核對；沒有跟隨到其他服務。", "waiting_capability",{'from_url':url,'location':target,'status':exc.code})
        raise WorkflowError("公開來源回傳HTTP " + str(exc.code), "tool_rejected")
    except (URLError,TimeoutError) as exc:
        raise WorkflowError('公開來源暫時無法讀取；保存同一URL供有界重試。','transient',details={'url':url,'transport_error':type(exc).__name__}) from exc


class ToolExecutor:
    """One real invocation with write-ahead policy, readback and a task trace."""
    def __init__(self, store, config):
        self.store, self.config = store, config

    def route(self, ident):
        route = self.config.get("tool_routes", {}).get(ident)
        if not isinstance(route, dict) or route.get("enabled") is not True:
            raise Waiting("尚未連接並啟用這個實際工具路徑。", "waiting_capability", {"route_id": ident})
        if route.get("mode") not in ("read", "write", "local"):
            raise ValueError("Tool route must declare read/write/local")
        return route

    def clean(self, value, route):
        names = set(route.get('env_allow', [])) | set(route.get('redact_env', []))
        secrets = [os.environ[name] for name in names if name in os.environ
            and (name in route.get('redact_env', []) or re.search(r'TOKEN|SECRET|PASSWORD|CREDENTIAL|API_KEY', name, re.I))]
        return redact(value, secrets)

    def usage_gate(self,route):
        if route.get('channel')!='x' and route.get('metered') is not True:return
        from .contracts import _proof
        usage=route.get('usage',{})
        stamp=usage.get('checked_at')
        quota=usage.get('quota_remaining')
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or not self.store.clock()-3600<=stamp<=self.store.clock()+300 or not _proof(usage.get('source_ref'),'live') or type(quota) is not int or quota<1:
            raise Waiting('此工具尚缺本期API費用／配額讀回，不把付費讀取當免費。','waiting_capability')
        if type(usage.get('maximum_cost_minor')) is not int or usage['maximum_cost_minor']!=0 or usage.get('includes_probe_and_readback') is not True:
            raise Waiting('此付費工具需已配置的宿主計費執行器承接；本共用客戶端不自行新增費用。','waiting_approval')

    def _call(self, route, name, arguments, before_dispatch=None):
        if route.get('transport')=='builtin_media':
            from .files import safe_path
            from .media import verify_image,normalized_image
            from .video import make_video,verify_video
            if before_dispatch:before_dispatch()
            if name=='media_capability':
                from .media import dependency_status
                available=dependency_status()
                if not available['image_package_available'] or not available['video_package_available']:
                    raise Waiting('此主機缺選配影像／影片套件；核心仍可執行其他工作。','waiting_capability',available)
                import PIL,imageio_ffmpeg
                try:
                    decoder=imageio_ffmpeg.get_ffmpeg_exe()
                except RuntimeError as exc:
                    raise Waiting('此主機沒有可用的影片解碼工具。','waiting_capability',available) from exc
                return {'image_library':PIL.__version__,'ffmpeg_available':bool(decoder),'observed_at':self.store.clock()}
            source=safe_path(self.store.root,arguments['source'])
            if name=='verify_image':return verify_image(source)
            if name=='verify_video':return verify_video(source)
            if route.get('mode')!='local':raise ValueError('Media transforms are local operations, not read-only probes')
            output=safe_path(self.store.root,arguments['output'])
            if output==source:raise ValueError('Keep the original media; output must be a different file')
            size=tuple(arguments.get('size',[1080,1920] if name=='make_video' else [1080,1350]))
            if name=='normalize_image':return normalized_image(source,output,size)
            if name=='make_video':return make_video(source,output,arguments.get('duration',6),size)
            raise ValueError('Unknown built-in media operation')
        if route.get("transport") == "builtin_public_web":
            if route.get("mode") != "read" or name != "fetch_public" or set(arguments) != {"url"}:
                raise ValueError("Public reader supports only the exact read operation")
            if 'allowed_urls' in route and (not isinstance(route['allowed_urls'],list) or arguments['url'] not in route['allowed_urls']):
                raise WorkflowError('Source URL is outside the original exact URL allowlist.','scope_mismatch')
            if before_dispatch:before_dispatch()
            return fetch_public(arguments["url"], route.get("allowed_hosts", []), route.get("fixture_only") is True)
        if route.get("transport") != "mcp_stdio":
            raise Waiting("需要實際MCP stdio工具，或宿主已授權工具回執；沒有虛構平台API。", "waiting_capability")
        client = MCPStdioClient(route)
        try:
            return client.call(name, arguments, before_dispatch)
        except WorkflowError as exc:
            exc.details=self.clean(exc.details,route)
            raise
        finally:
            client.close()

    def probe(self, run_id, route_id):
        profile=self.store.config(run_id)
        route=self.route(route_id)
        self.usage_gate(route)
        if profile['brand']['url'] not in route.get('brand_urls',[]):
            raise WorkflowError('此工具未綁定本品牌。','scope_mismatch')
        if route.get('fixture_only') and profile.get('mode')!='fixture':
            raise WorkflowError('受控工具不能驗證真實連接。','scope_mismatch')
        if profile.get('mode')=='fixture' and not route.get('fixture_only'):
            raise WorkflowError('受控驗收只能探測隔離工具。','scope_mismatch')
        probe=route.get('probe')
        if not isinstance(probe,dict) or probe.get('read_only') is not True:
            raise Waiting('工具需要明確的唯讀身分／來源探測；不以工具清單當執行成功。','waiting_capability')
        # A newer failed access attempt invalidates the older successful probe.
        self.store.artifact(run_id,0,'tool.probe.'+digest(route_id),{'route_sha256':digest(route),'state':'verification_pending',
            'inventory':{'tool_id':route_id,'channel':route.get('channel'),'capabilities':[],'account_ref':None,'actions':[],'verified_at':0,'evidence':[]}})
        raw=self._call({**route,'mode':'read'},probe['tool_name'],probe.get('arguments',{}))
        account=pointer(raw,probe['account_pointer']) if probe.get('account_pointer') else None
        channel=route.get('channel')
        selected=profile.get('bindings',{}).get(channel,{}).get('account_ref')
        if channel not in ('web','local','audience') and (not selected or account!=selected):
            return {'state':'account_pending','route_id':route_id,'account_ref':account,'selected_account_ref':selected,'actual_read_performed':True}
        if probe.get('connected_pointer') and pointer(raw,probe['connected_pointer']) not in (True,'connected'):
            return {'state':'account_pending','route_id':route_id,'actual_read_performed':True}
        if probe.get('scopes_pointer'):
            scopes=pointer(raw,probe['scopes_pointer'])
            if not isinstance(scopes,list) or not set(probe.get('required_scopes',[])).issubset(scopes):
                return {'state':'scope_pending','route_id':route_id,'actual_read_performed':True}
        proof=route.get('evidence_url') or raw.get('url')
        if channel=='local' and not proof:proof='event:tool.probe.'+digest({'route':route_id,'result':raw})
        from .contracts import _proof
        if not isinstance(proof,str) or not _proof(proof,profile.get('mode','prepare')):
            raise ValueError('A native tool probe needs its actual source reference')
        inventory={'tool_id':route_id,'capabilities':route.get('capabilities',[]),'channel':channel,
            'account_ref':account,'actions':route.get('actions',[]),'verified_at':self.store.clock(),
            'evidence':[{'reference':proof,'observed_at':self.store.clock()}]}
        value={'route_sha256':digest(route),'inventory':inventory,'state':'identity_or_source_read_verified',
            'write_execution_verified':False,'goal_outcome_verified':False}
        self.store.artifact(run_id,0,'tool.probe.'+digest(route_id),value)
        return value

    def execute(self, request, route_id, arguments=None, action=None, lease_task=None):
        from .actions import ActionManager
        from .contracts import CAPABILITIES
        lease_task = lease_task or self.store.task(request["task_key"])
        self.store.assert_lease(lease_task)
        profile = self.store.config(request["run_id"])
        route = self.route(route_id)
        self.usage_gate(route)
        skill = request.get("channel_skill_id") or request["skill_id"]
        if skill not in route.get("skill_ids", []):
            raise WorkflowError("工具路徑未綁定這個工作契約。", "scope_mismatch")
        if request["brand_url"] not in route.get("brand_urls", []):
            raise WorkflowError("工具路徑不是這個品牌。", "scope_mismatch")
        if route.get("fixture_only") and profile.get("mode") != "fixture":
            raise WorkflowError("受控工具不能用在真實工作。", "scope_mismatch")
        if profile.get('mode')=='fixture' and not route.get('fixture_only'):
            raise WorkflowError('受控工作只能使用明確隔離的工具。','scope_mismatch')
        writing = route["mode"] == "write"
        if writing != bool(action):
            raise ValueError("Writes need an exact scoped action; reads cannot contain one")
        if writing and (not route.get("readback") or not route["readback"].get("account_pointer")):
            raise Waiting("寫入工具缺相同帳號的實際讀回步驟。", "waiting_capability")
        if writing and action.get('action')=='reply_message' and not route.get('original_read'):
            raise Waiting('回覆工具需原串即時讀取及直接父物件讀回，不能只有新貼文工具。','waiting_capability')
        channel = route.get("channel")
        if request.get("channel_skill_id"):
            from .channels import channel_skill
            if channel_skill(skill)["channel"] != channel:
                raise WorkflowError("渠道工作與工具路徑不符。", "scope_mismatch")
        if channel not in ('web','local','audience'):
            saved=self.store.artifact(request['run_id'],0,'tool.probe.'+digest(route_id))
            inventory=(saved or {}).get('inventory',{})
            account=profile.get('bindings',{}).get(channel,{}).get('account_ref')
            if not saved or saved.get('state')!='identity_or_source_read_verified' or saved.get('route_sha256')!=digest(route) or not account or inventory.get('account_ref')!=account or not self.store.clock()-3600<=inventory.get('verified_at',0)<=self.store.clock()+300:
                raise Waiting('先對此工具讀回相同品牌帳號與有效權限。','waiting_capability')
        ctx = {"request": request, "action": action, "arguments": arguments or {}}
        if writing:
            if action.get("channel") != channel or action.get("action") not in route.get("actions", []):
                raise WorkflowError("實際工具不支援這個已準備動作。", "scope_mismatch")
            if action.get("account_ref") != route.get("account_ref"):
                raise WorkflowError("工具與動作帳號不符。", "scope_mismatch")
            native_args = None
        else:
            native_args = arguments or {}
            if not isinstance(native_args, dict):
                raise ValueError("Tool arguments must be an object")
            if route.get("account_pointer"):
                actual = pointer(native_args, route["account_pointer"])
                if actual != profile.get("bindings", {}).get(channel, {}).get("account_ref"):
                    raise WorkflowError("唯讀工具的目標帳號不符。", "scope_mismatch")
        permit = ActionManager(self.store).prepare(request, action) if writing else None
        if permit and not permit["execute"]:
            return {"already_completed": True, "result": permit["result"]}
        if permit:
            ctx['operation_key'] = permit['operation_key']
        dispatched=False
        started_at=self.store.clock()
        started_monotonic=time.monotonic()
        schema=self.store.artifact(request['run_id'],0,'capability.schema.'+digest(route_id)) or {}
        schema_sha=(schema.get('schema_sha256') if schema.get('state')=='schema_verified' and schema.get('route_sha256')==digest(route) and self.store.clock()-3600<=schema.get('observed_at',0)<=self.store.clock()+300 else None)
        attempt_id='tool.attempt.'+digest({'task':request['task_key'],'route':route_id,'started_at':started_at,'nonce':time.monotonic_ns()})
        attempt={'task_key':request['task_key'],'skill_id':skill,'route_id':route_id,'route_sha256':digest(route),'schema_sha256':schema_sha,
            'state':'pending','started_at':started_at,'mode':route['mode'],'operation_key':permit['operation_key'] if permit else None,
            'source':'controlled_fixture' if profile.get('mode')=='fixture' else 'live_tool'}
        self.store.artifact(request['run_id'],request['cycle'],attempt_id,attempt)
        from .procedure_program import selected_input_examples
        learning_inputs = self.clean(selected_input_examples(native_args or {}, route), route)
        try:
            if not writing:
                assignment = self.store.artifact(request['run_id'], 0, 'procedure.assignment.' + digest(request['task_key'])) or {}
                procedure = assignment.get('procedure') or {}
                program = procedure.get('program', [])
                if program:
                    context = procedure['context']
                    expected = {'skill_id': skill, 'route_id': route_id, 'route_sha256': digest(route), 'schema_sha256': schema_sha,
                                'account_ref': profile.get('bindings', {}).get(channel, {}).get('account_ref')}
                    if any(context.get(key) != value for key, value in expected.items()):
                        raise WorkflowError('學習程序與目前技能、工具、schema或帳號不符。', 'scope_mismatch')
                    from .procedure_program import apply_program
                    native_args = apply_program(native_args, program, route)
                if route.get('account_pointer') and pointer(native_args,route['account_pointer']) != profile.get('bindings',{}).get(channel,{}).get('account_ref'):
                    raise WorkflowError('學習程序後的唯讀目標帳號不符。','scope_mismatch')
            if writing and action.get('action')!='reply_message':
                native_args=bind(route.get('arguments',{}),ctx)
            def current():
                if (self.store.root / '.transfer.lock').exists():
                    raise Waiting('工作區正在搬移；保留原工作，不呼叫平台。', 'waiting_capability')
                if self.store.artifact(request['run_id'],0,'graph.stopped'):
                    raise WorkflowError('工作已停止，不再呼叫平台。','scope_mismatch')
                # Fence against the lease captured before connector discovery.
                # Reading the latest task here would accept another worker's lease.
                self.store.assert_lease(lease_task)
                if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_leases'").fetchone():
                    self.store.db.execute('UPDATE worker_leases SET expires=? WHERE task_key=? AND lease=?',
                        (self.store.clock()+180, request['task_key'], lease_task['lease']))
            def write_allowed():
                nonlocal dispatched
                current()
                from .policy import Policy
                from .files import read_json
                grant_path=self.store.root/'authorization.json'
                grant=read_json(grant_path) if grant_path.exists() else {}
                Policy(self.store.config(request['run_id']),grant,self.store.clock).require(action['action'],action['account_ref'],public=action['action'] in ('publish_content','update_content','publish_listing','update_listing'),spending=action['action']=='activate_ads')
                dispatched=True
            original=None
            if writing and action.get('action')=='reply_message':
                refresh=route['original_read']
                original=self._call({**route,'mode':'read'},refresh['tool_name'],bind(refresh['arguments'],ctx),current)
                if pointer(original,refresh['url_pointer'])!=action.get('target',{}).get('url'):
                    raise ValueError('Original reply thread does not match the scoped source URL')
                ctx['original']=original
                native_args=bind(route.get('arguments',{}),ctx)
            if writing:
                # Persist the lookup cursor before dispatch, including when
                # the provider commits but its response never arrives.
                self.store.artifact(request['run_id'],request['cycle'],'tool.write_cursor.'+digest(permit['operation_key']),{
                    'route_id':route_id,'route_sha256':digest(route),'result':None,'original':self.clean(original,route),
                    'operation_key':permit['operation_key'],'observed_at':self.store.clock()})
            raw = self._call(route, route["tool_name"], native_args,write_allowed if writing else current)
            if writing:
                self.store.artifact(request['run_id'],request['cycle'],'tool.write_cursor.'+digest(permit['operation_key']),{
                    'route_id':route_id,'route_sha256':digest(route),'result':self.clean(raw,route),'original':self.clean(original,route),'operation_key':permit['operation_key'],'observed_at':self.store.clock()})
                ctx["result"] = raw
                verify = route["readback"]
                readback = self._call({**route, "mode": "read"}, verify["tool_name"], bind(verify["arguments"], ctx),current)
                if pointer(readback, verify["account_pointer"]) != action["account_ref"]:
                    raise ValueError("Readback account mismatch")
                if action.get("text") is not None and (not verify.get("text_pointer") or pointer(readback, verify["text_pointer"]) != action["text"]):
                    raise ValueError("Readback exact text mismatch")
                if original is not None and (not verify.get('reply_parent_pointer') or pointer(readback,verify['reply_parent_pointer'])!=pointer(original,route['original_read']['id_pointer'])):
                    raise ValueError('Reply is not directly attached to the original requested object')
                external_id = pointer(readback, verify["id_pointer"])
                status = str(pointer(readback, verify["status_pointer"])).lower()
                expected = {"create_paused_ads": {"paused"}, "activate_ads": {"active"}, "pause_ads": {"paused"}}
                if action["action"] in expected and status not in expected[action["action"]]:
                    raise ValueError("Readback effective state mismatch")
                result = {"source": "controlled_fixture" if profile.get("mode") == "fixture" else "live_tool",
                    "observed_at": self.store.clock(), "account_ref": action["account_ref"],
                    "payload_sha256": permit["payload_sha256"], "external_id": external_id, "status": status,
                    "evidence": [{"reference": route["evidence_url"], "observed_at": self.store.clock()}]}
                if verify.get("url_pointer"):
                    result["url"] = pointer(readback, verify["url_pointer"])
                if verify.get('effective_status_pointer'):
                    result['effective_status'] = str(pointer(readback,verify['effective_status_pointer'])).upper()
                current()
                ActionManager(self.store).result(request["run_id"], permit["operation_key"], result)
                raw = {"write_result": raw, "readback": readback, "operation_result": result}
        except Exception as exc:
            failure={'route_id':route_id,'tool_name':route['tool_name'],'skill_id':skill,'task_key':request['task_key'],
                'learning_inputs':learning_inputs,
                'route_sha256':digest(route),'schema_sha256':schema_sha,'transport':route['transport'],
                'attempt_ref':attempt_id,
                'started_at':started_at,'duration_ms':round((time.monotonic()-started_monotonic)*1000,3),
                'arguments_sha256':digest(native_args),'kind':getattr(exc,'kind','tool_error'),
                'error_type':type(exc).__name__,'message':self.clean(str(exc),route)[:600],
                'details':self.clean(getattr(exc,'details',{}),route),'observed_at':self.store.clock(),
                'operation_key':permit['operation_key'] if permit else None,'dispatched':dispatched,
                'source':'controlled_fixture' if profile.get('mode')=='fixture' else 'live_tool'}
            failure_id='tool.failure.'+digest(failure)
            self.store.artifact(request['run_id'],request['cycle'],failure_id,failure)
            self.store.artifact(request['run_id'],request['cycle'],attempt_id,{**attempt,'state':'failed','trace_id':failure_id,'finished_at':self.store.clock()})
            if permit:
                uncertain = {"source": "controlled_fixture" if profile.get("mode") == "fixture" else "live_tool",
                    "observed_at": self.store.clock(), "account_ref": action["account_ref"],
                    "payload_sha256": permit["payload_sha256"], "status": "uncertain" if dispatched else "rejected"}
                # Only failures before tools/call are known not to have sent.
                # Once dispatched, keep the original cursor for reconciliation.
                ActionManager(self.store).result(request["run_id"], permit["operation_key"], uncertain)
            from .experience import Experience
            Experience(self.store,self.config).capture(request['run_id'],failure_id)
            raise
        trace = {"route_id": route_id, "tool_name": route["tool_name"], "skill_id": skill,
            'learning_inputs':learning_inputs,
            'route_sha256':digest(route),'schema_sha256':schema_sha,
            'attempt_ref':attempt_id,
            'started_at':started_at,'duration_ms':round((time.monotonic()-started_monotonic)*1000,3),
            "task_key": request["task_key"], "request_sha256": request["request_sha256"],
            "arguments_sha256": digest(native_args), "result": self.clean(raw,route),
            "observed_at": self.store.clock(), "source": "controlled_fixture" if profile.get("mode") == "fixture" else "live_tool",
            "transport": route["transport"], "operation_key": permit["operation_key"] if permit else None}
        ident = "tool.execution." + digest(trace)
        self.store.artifact(request["run_id"], request["cycle"], ident, trace)
        self.store.artifact(request['run_id'],request['cycle'],attempt_id,{**attempt,'state':'succeeded','trace_id':ident,'finished_at':self.store.clock()})
        self.store.event(request["run_id"], request["task_key"], "tool_executed", {"trace": ident, "route_id": route_id})
        from .experience import Experience
        Experience(self.store,self.config).capture(request['run_id'],ident)
        return {"trace_id": ident, "evidence_ref": "event:" + ident, **trace}

    def reconcile(self,request,route_id,operation_key,lease_required=True,lease_task=None):
        """Only GET/readback; this method never calls the publish/send tool."""
        from .actions import ActionManager
        op=self.store.operation(operation_key)
        if not op or op['task_key']!=request['task_key']:
            raise WorkflowError('對帳操作不屬於本工作。','scope_mismatch')
        if op['state']=='succeeded':return {'already_completed':True,'result':op['result']}
        route=self.route(route_id)
        self.usage_gate(route)
        cursor=self.store.artifact(request['run_id'],request['cycle'],'tool.write_cursor.'+digest(operation_key))
        if not cursor or cursor.get('route_id')!=route_id or cursor.get('route_sha256')!=digest(route):
            raise Waiting('尚缺原工具的物件ID／相符讀回路徑；保留未知操作，不重送。','waiting_reconcile',{'operation_key':operation_key})
        verify=route.get('readback',{}) if cursor.get('result') is not None else route.get('uncertain_readback')
        if not verify or not verify.get('account_pointer'):
            raise Waiting('原回應遺失且平台工具缺依操作識別查回的唯讀路徑；不重送。','waiting_reconcile',{'operation_key':operation_key})
        action=op['request']
        if action.get('account_ref')!=route.get('account_ref') or request['brand_url'] not in route.get('brand_urls',[]):
            raise WorkflowError('對帳帳號／品牌不符。','scope_mismatch')
        lease_task=lease_task or self.store.task(request['task_key'])
        def current():
            task=self.store.task(request['task_key'])
            if lease_required:self.store.assert_lease(lease_task)
            elif task['state']=='running' and (task.get('lease_until') or 0)>self.store.clock():
                raise WorkflowError('對帳期間已有其他worker持有原任務。','lease_lost')
        current()
        raw=self._call({**route,'mode':'read'},verify['tool_name'],bind(verify['arguments'],{'request':request,'action':action,'result':cursor['result'],'original':cursor.get('original'),'operation_key':operation_key}),current)
        if pointer(raw,verify['account_pointer'])!=action['account_ref'] or action.get('text') is not None and pointer(raw,verify['text_pointer'])!=action['text']:
            raise WorkflowError('原物件讀回內容／身分不符。','readback_mismatch')
        if action['action']=='reply_message' and (not cursor.get('original') or pointer(raw,verify['reply_parent_pointer'])!=pointer(cursor['original'],route['original_read']['id_pointer'])):
            raise WorkflowError('回覆尚未讀回原始直接父物件。','readback_mismatch')
        result={'source':'controlled_fixture' if self.store.config(request['run_id']).get('mode')=='fixture' else 'live_tool',
            'observed_at':self.store.clock(),'account_ref':action['account_ref'],'payload_sha256':op['fingerprint'],
            'external_id':pointer(raw,verify['id_pointer']),'status':str(pointer(raw,verify['status_pointer'])).lower(),
            'evidence':[{'reference':route['evidence_url'],'observed_at':self.store.clock()}]}
        expected={'create_paused_ads':{'paused'},'activate_ads':{'active'},'pause_ads':{'paused'}}
        if action['action'] in expected and result['status'] not in expected[action['action']]:
            raise WorkflowError('讀回的有效狀態尚未符合原操作。','readback_mismatch')
        if verify.get('url_pointer'):result['url']=pointer(raw,verify['url_pointer'])
        if verify.get('effective_status_pointer'):result['effective_status']=str(pointer(raw,verify['effective_status_pointer'])).upper()
        current()
        return ActionManager(self.store).result(request['run_id'],operation_key,result)
