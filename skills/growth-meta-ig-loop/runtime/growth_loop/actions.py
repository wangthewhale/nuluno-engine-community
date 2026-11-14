"""Shared write-ahead permits, contact locks and verified action readbacks."""
import json
import math
import os
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, quote
from urllib.request import Request, build_opener

from .contracts import ALLOWED_ACTIONS
from .errors import Waiting, WorkflowError, UncertainWrite
from .files import atomic_json, canonical, digest, read_json, redact
from .policy import Policy
from .sales_state import SalesState
from .transport import NoRedirect

CONTACT_ACTIONS = {"send_message", "reply_message", "deliver_resource"}
NEVER_AGENT_ACTIONS = {"accept_contract", "sign_contract", "change_product", "change_price", "change_checkout", "buy_service", "grant_credentials"}


class ActionManager:
    def __init__(self, store):
        self.store, self.sales = store, SalesState(store)

    def _scope(self, request, action, reserve=False, operation_key=None):
        profile = self.store.config(request["run_id"])
        if request["brand_url"] != profile["brand"]["url"]:
            raise WorkflowError("工作不是此品牌。", "scope_mismatch")
        kind = action.get("action")
        task = self.store.task(request['task_key'])
        expected_channel=(task or {}).get('data',{}).get('channel')
        if task and (profile.get('workflow',{}).get('id')=='goal' or task['data'].get('role')=='followup') and expected_channel not in (None,'','common','local','audience','review') and action.get('channel')!=expected_channel:
            raise WorkflowError('目標／跟進只能操作原渠道；換渠道需另一份工作與範圍。','scope_mismatch')
        if task and task['data'].get('channel_skill_id'):
            from .channels import channel_skill
            spec=channel_skill(task['data']['channel_skill_id'])
            if spec['channel'] in ('x','linkedin') and profile.get('bindings',{}).get(spec['channel'],{}).get('automation_route')!='approved_api':
                raise Waiting('此平台自動寫入需已核對的允許API；網站腳本不是替代方案。','waiting_capability')
            if action.get('channel')!=spec['channel'] or kind not in spec['allowed_actions']:
                raise WorkflowError('對外動作不符合本渠道細項工作。','scope_mismatch')

        if kind in NEVER_AGENT_ACTIONS or kind not in ALLOWED_ACTIONS.get(request["skill_id"], set()):
            raise WorkflowError("這個Skill不能執行此動作或替使用者做新承諾。", "scope_mismatch")
        channel, account = action.get("channel"), action.get("account_ref")
        binding = profile.get("bindings", {}).get(channel, {})
        creation = kind == "create_marketing_account"
        selected = binding.get("creation_parent_ref") if creation else binding.get("account_ref")
        if not channel or not account or selected != account:
            raise Waiting("尚未選定這個品牌的渠道身分。", "waiting_capability")
        if creation and (binding.get("account_ref") or not binding.get("creation_parent_evidence")):
            raise Waiting("先沿用現有帳號；若尚未建立，需核對本輪可用的本人／商務建立入口。", "waiting_capability")
        grant_path = self.store.root / "authorization.json"
        grant = read_json(grant_path) if grant_path.exists() else {}
        task = self.store.task(request["task_key"])
        expiry_guard = bool((task or {}).get("data", {}).get("expiry_guard"))
        if (task or {}).get('data', {}).get('role') == 'followup':
            scheduled = task['data']
            if action.get('followup_id') != scheduled['followup_id'] or action.get('target', {}).get('contact_id') != scheduled['contact_id']:
                raise WorkflowError('到期工作只能處理原跟進與原對象。', 'scope_mismatch')
        policy_account = account
        if not creation:
            for row in self.store.db.execute("SELECT request,result FROM operations WHERE task_key LIKE ? AND state='succeeded'", (request["run_id"] + ":%",)):
                prior, readback = json.loads(row[0]), json.loads(row[1])
                if prior.get("action") == "create_marketing_account" and prior.get("channel") == channel and readback.get("created_account_ref") == account:
                    policy_account = prior["account_ref"]
                    break
        Policy(profile, grant, self.store.clock).require(kind, policy_account, public=kind in ("publish_content", "update_content", "publish_listing", "update_listing"), spending=kind == "activate_ads", allow_expired_stop=expiry_guard and kind == "pause_ads")
        identity = self.store.artifact(request["run_id"], 0, "connected_accounts") or {"accounts": []}
        if not creation and not any(a.get("channel") == channel and a.get("account_ref") == account and a.get("state") == "connected" for a in identity["accounts"]):
            raise Waiting("尚未讀回本人管理這個品牌帳號的證據。", "waiting_capability")
        cost = action.get("cost_minor", 0)
        if type(cost) is not int or cost < 0:
            raise WorkflowError("動作費用需是已核對的非負最小貨幣整數。", "invalid_input")
        if cost:
            if action.get("currency") != profile.get("budget", {}).get("currency"):
                raise WorkflowError("動作費用幣別未核對。", "scope_mismatch")
            if "incur_cost" not in grant.get("actions", []):
                raise Waiting("這輪沒有新增費用授權。", "waiting_approval")
            cap = min(profile.get("budget", {}).get("total_minor", 0), grant.get("total_minor", 0))
            committed = 0
            for row in self.store.db.execute("SELECT request,state FROM operations WHERE task_key LIKE ? AND state IN ('in_flight','uncertain','succeeded')", (request["run_id"] + ":%",)):
                previous = json.loads(row[0])
                committed += previous.get("cost_minor", 0)
            if cost + committed > cap:
                raise Waiting("這輪已承諾與未確認費用加總會超過上限。", "waiting_approval")
            recent = 0
            for row in self.store.db.execute("SELECT request FROM operations WHERE task_key LIKE ? AND state IN ('in_flight','uncertain','succeeded') AND updated>=?", (request["run_id"] + ":%", self.store.clock() - 86400)):
                recent += json.loads(row[0]).get("cost_minor", 0)
            if cost + recent > profile.get("budget", {}).get("daily_minor", 0):
                raise Waiting("最近24小時已承諾與未確認的費用會超出每日上限。", "waiting_approval")
        if kind == 'update_ads':
            # Updates cannot smuggle activation or provider-specific money fields.
            allowed = {'action', 'channel', 'account_ref', 'target', 'desired_status', 'cost_minor',
                       'currency', 'daily_budget_minor', 'total_budget_minor', 'text', 'asset_sha256', 'reason'}
            if set(action) - allowed or action.get('desired_status', 'PAUSED') not in ('PAUSED', 'DRAFT'):
                raise WorkflowError('update_ads只允許暫停修改；啟用需獨立activate_ads。', 'scope_mismatch')
            if set(action.get('target', {})) - {'object_ids'}:
                raise WorkflowError('廣告目標不可夾帶平台更新欄位。', 'scope_mismatch')
        if kind == "create_paused_ads" and action.get("desired_status", "PAUSED") not in ("PAUSED", "DRAFT"):
            raise WorkflowError("建立暫停廣告不能偷帶ACTIVE。", "scope_mismatch")
        if kind in ("activate_ads", "pause_ads", "update_ads"):
            owned = set(grant.get("object_ids", []))
            for row in self.store.db.execute("SELECT request,result FROM operations WHERE task_key LIKE ? AND state='succeeded'", (request["run_id"] + ":%",)):
                prior, result = json.loads(row[0]), json.loads(row[1])
                if prior.get("action") == "create_paused_ads" and prior.get("account_ref") == account:
                    owned.add(result["external_id"])
                    owned.update(result.get("object_ids", []))
            targets = action.get("target", {}).get("object_ids", [])
            if not targets or not set(targets).issubset(owned):
                raise WorkflowError("只能變更這輪建立或使用者明確指定的廣告，不能操作帳號內其他工作。", "scope_mismatch")
            if kind in ("activate_ads", "update_ads"):
                for field, cap_field in (("daily_budget_minor", "daily_minor"), ("total_budget_minor", "total_minor")):
                    amount = action.get(field)
                    if (kind == "activate_ads" and amount is None) or (amount is not None and (type(amount) is not int or amount < 0 or amount > profile.get("budget", {}).get(cap_field, 0))):
                        raise WorkflowError("需提供已核對且不超出範圍的每日／整輪廣告金額。", "scope_mismatch")
        if kind in CONTACT_ACTIONS:
            target = action.get("target", {})
            contact_id = target.get("contact_id")
            purpose = action.get("purpose", "outreach")
            transport=action.get('transport',channel)
            if channel in ('partnerships','earned_media','reviews'):
                if transport not in ('email','line','whatsapp','linkedin','discord','facebook_groups','web_form'):
                    raise WorkflowError('商務方法需明確實際訊息渠道，不能以方法分類繞過聯絡限制。','scope_mismatch')
                if transport!='web_form' and profile.get('bindings',{}).get(transport,{}).get('account_ref')!=account:
                    raise Waiting('實際訊息渠道身分尚未對上本品牌商務帳號。','waiting_capability')
            if transport in ('line','whatsapp'):
                from .contracts import _proof
                checked=action.get('cost_checked_at')
                if 'cost_minor' not in action or not _proof(action.get('cost_evidence'),profile.get('mode','prepare')) or type(checked) not in (int,float) or not math.isfinite(checked) or not self.store.clock()-3600<=checked<=self.store.clock()+300:
                    raise Waiting('此訊息需本輪配額／費用讀回；未知費用不能當0。','waiting_capability')
            require_opt_in = transport in ("email", "line", "whatsapp") or action.get("requires_opt_in", False)
            contact = self.sales.can_contact(profile["brand"]["url"], contact_id, purpose, action.get("source_event_id"), require_opt_in)
            destination = target.get("address") if transport == "email" else target.get("url") or target.get("identity")
            allowed = {contact["identity"], contact.get("source_url"), *contact.get("allowed_reply_urls", [])}
            if not destination or destination not in allowed:
                raise WorkflowError("訊息目的地與已核對對象不符。", "scope_mismatch")
            if profile.get("acquisition", {}).get("raised_hand_only"):
                if transport == "email" or contact.get("signal_status") != "qualified_for_relevant_reply" or purpose not in ("public_reply", "reply"):
                    raise WorkflowError("此需求找客流程只回明確舉手的原串，不做冷郵件。", "scope_mismatch")
            if not action.get("text") and not action.get("resource_url"):
                raise WorkflowError("沒有實際訊息或資源內容。", "invalid_input")
            if action.get("followup_id"):
                followup = self.store.db.execute("SELECT * FROM followups WHERE id=? AND brand=? AND contact_id=?", (action["followup_id"], profile["brand"]["url"], contact_id)).fetchone()
                if not followup or followup["state"] != "pending" or followup["due"] > self.store.clock():
                    raise Waiting("此跟進尚未到期或已取消，不送出。", "waiting_contact")
            if reserve:
                limits = profile.get("contact_limits", {})
                self.sales.reserve_contact(profile["brand"]["url"], contact_id, operation_key, purpose,
                    limits.get("max_per_day", 20), limits.get("cooldown_seconds", 86400), action.get("source_event_id"),
                    require_opt_in, context_url=target.get("url"))
        return profile

    def prepare(self, request, action):
        clean = {k: request[k] for k in ("run_id", "brand_url", "task_key", "cycle", "skill_id", "stage_order", "stage_action", "input_digest")}
        if digest(clean) != request.get("request_sha256"):
            raise WorkflowError("工作請求內容已被修改。", "payload_changed")
        task = self.store.task(request["task_key"])
        if not task or task["run_id"] != request["run_id"] or task["state"] not in ("waiting_agent", "running", "waiting_action"):
            raise WorkflowError("沒有相符的待執行工作。", "invalid_task")
        if task["data"].get("skill_id") != request["skill_id"]:
            raise WorkflowError("動作不是本階段的Skill。", "scope_mismatch")
        saved = self.store.artifact(task["run_id"], task["cycle"], "flow_request." + digest(task["key"])[:20])
        if not saved or saved["request_sha256"] != request["request_sha256"]:
            raise WorkflowError("這不是目前保存的工作請求。", "payload_changed")
        key = request["task_key"] + ":action:" + digest(action)[:24]
        fingerprint = digest(action)
        with self.store.transaction():
            old = self.store.operation(key)
            if old and old["fingerprint"] != fingerprint:
                raise WorkflowError("同一動作內容已改變。", "payload_changed")
            if old and old["state"] == "succeeded":
                return {"operation_key": key, "execute": False, "already_completed": True, "result": old["result"]}
            if old and old["state"] in ("in_flight", "uncertain"):
                raise Waiting("前次動作結果不明，先依同一操作識別對帳，不重送。", "waiting_reconcile", {"operation_key": key})
            self._scope(request, action, True, key)
            # prepare_operation opens its own transaction; persist here atomically
            # with the contact reservation using the same connection instead.
            self.store.db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET state='in_flight',updated=excluded.updated", (key, task["key"], fingerprint, "in_flight", canonical(redact(action)), None, self.store.clock()))
            self.store.event(request["run_id"], task["key"], "external_action_permitted", {"operation_key": key, "action": action["action"], "account_ref": action["account_ref"]})
        permit = {"operation_key": key, "request_sha256": request["request_sha256"], "payload_sha256": fingerprint,
                  "expires_at": self.store.clock() + 120, "execute": True, "action": action}
        atomic_json(self.store.root / "action-permits" / (digest(key) + ".json"), permit, private=True)
        return permit

    def result(self, run_id, operation_key, result):
        op = self.store.operation(operation_key)
        if not op or not operation_key.startswith(run_id + ":"):
            raise WorkflowError("操作回執不屬於此工作。", "scope_mismatch")
        if op["state"] == "succeeded":
            if digest(op["result"]) != digest(redact(result)):
                raise WorkflowError("已完成動作不能替換回執。", "payload_changed")
            return {"recorded": True, "duplicate": True, "operation_key": operation_key}
        profile = self.store.config(run_id)
        expected_source = "controlled_fixture" if profile.get("mode") == "fixture" else "live_tool"
        if result.get("source") != expected_source or not result.get("observed_at"):
            raise WorkflowError("需要實際工具讀回；受控資料與真實資料不可混用。", "invalid_result")
        if type(result["observed_at"]) not in (int, float) or not math.isfinite(result["observed_at"]):
            raise WorkflowError("操作回執時間不是有效數字。", "invalid_result")
        if not op['updated']-300<=result['observed_at']<=self.store.clock()+300:
            raise WorkflowError('操作回執時間不屬於這次原操作。','invalid_result')
        if result.get("payload_sha256") != op["fingerprint"] or result.get("account_ref") != op["request"].get("account_ref"):
            raise WorkflowError("實際內容或品牌身分與預備動作不符。", "readback_mismatch")
        if result.get("status") == "uncertain":
            self.store.set_operation(operation_key, "uncertain", result)
            raise UncertainWrite("未確認外部結果，保留原工作並對帳。", {"operation_key": operation_key})
        if result.get("status") == "rejected":
            self.store.set_operation(operation_key, "rejected", result)
            self.store.db.execute("UPDATE contact_reservations SET state='released' WHERE operation_key=?", (operation_key,))
            return {"recorded": True, "rejected": True, "operation_key": operation_key}
        if not result.get("external_id") or result.get("status") not in ("sent", "published", "submitted", "scheduled", "paused", "active", "delivered", "updated", "created") or not result.get("evidence"):
            raise WorkflowError("沒有可核對的外部結果、物件ID或證據。", "invalid_result")
        from .contracts import _proof
        if any(not _proof(e.get('reference') if isinstance(e,dict) else e,profile.get('mode','prepare')) for e in result['evidence']):
            raise WorkflowError('操作成功需可核對的原始來源，不接受任意成功文字。','invalid_result')
        expected={'create_paused_ads':{'paused'},'activate_ads':{'active'},'pause_ads':{'paused'}}
        kind=op['request']['action']
        if kind in expected and result['status'] not in expected[kind]:
            raise WorkflowError('平台有效狀態與原動作不同；建立暫停不等於啟用或配送。','readback_mismatch')
        if kind=='update_ads' and result['status']!='paused' and not (result['status']=='updated' and result.get('effective_status')=='PAUSED'):
            raise WorkflowError('廣告更新需讀回PAUSED，不能以其他狀態當已完成。','readback_mismatch')
        if op["request"].get("action") == "create_marketing_account" and not result.get("created_account_ref"):
            raise WorkflowError("建立帳號需要實際讀回新帳號ID，不能拿上層管理身分當新帳號。", "invalid_result")
        with self.store.transaction():
            self.store.set_operation(operation_key, "succeeded", result)
            if op["request"].get("action") == "create_marketing_account":
                profile["bindings"][op["request"]["channel"]]["account_ref"] = result["created_account_ref"]
                self.store.update_config(run_id, profile, created_account_operation=operation_key)
            self.sales.record_send(profile["brand"]["url"], operation_key, op["request"], result)
        self.store.event(run_id, op["task_key"], "external_action_verified", {"operation_key": operation_key, "external_id": result["external_id"], "status": result["status"], "source": result["source"]})
        return {"recorded": True, "operation_key": operation_key}


class ActionAdapter:
    """Optional user-configured action gateway, not a fictitious vendor API.

    POST /actions with an idempotency key; GET /operations/{key} reconciles.
    Without a configured gateway, the host uses its native connector/browser.
    """
    def __init__(self, profile):
        self.profile = profile
        self.config = profile.get("action_adapter", {})
        self.base = self.config.get("base_url", "").rstrip("/")
        p = urlparse(self.base)
        if profile.get("mode") == "fixture":
            valid = p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost")
        else:
            valid = p.scheme == "https" and p.hostname in self.config.get("allowed_hosts", [])
        if not valid or p.username or p.password or p.query or p.fragment:
            raise WorkflowError("操作介接端點未設定或不在允許範圍內。", "waiting_capability")
        token_env = self.config.get("token_env")
        if token_env in (profile.get("auth", {}).get("token_env", "META_ACCESS_TOKEN"), profile.get("auth", {}).get("app_secret_env", "META_APP_SECRET")):
            raise WorkflowError("不能把Meta密鑰送給其他操作端點。", "scope_mismatch")
        self.token = os.environ.get(token_env, "") if token_env else ""
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, value=None):
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token: headers["Authorization"] = "Bearer " + self.token
        body = canonical(value).encode() if value is not None else None
        try:
            with self.opener.open(Request(self.base + path, body, headers, method=method), timeout=self.config.get("timeout_seconds", 15)) as response:
                return json.loads(response.read(1024 * 1024))
        except HTTPError as exc:
            code, retry = exc.code, exc.headers.get("Retry-After", "30")
            exc.close()
            if code == 404 and method == "GET": return None
            if code == 429:
                raise WorkflowError("操作端點暫時限流。", "transient", retry_after=float(retry) if str(retry).isdigit() else 30)
            if code >= 500 and method == "POST": raise UncertainWrite("操作回應錯誤，先對帳。")
            raise WorkflowError("操作端點拒絕此要求，HTTP " + str(code), "waiting_capability" if code in (401, 403) else "permanent")
        except (OSError, URLError, socket.timeout, ValueError):
            if method == "POST": raise UncertainWrite("提交後連線中斷，結果未知。")
            raise WorkflowError("暫時無法讀取操作結果。", "transient")

    def execute(self, manager, request, action):
        key = request["task_key"] + ":action:" + digest(action)[:24]
        existing = manager.store.operation(key)
        if existing and existing["state"] in ("in_flight", "uncertain", "succeeded"):
            if existing["state"] == "succeeded": return existing["result"]
            found = self.request("GET", "/operations/" + quote(key, safe=""))
            if not found: raise Waiting("平台尚無唯一回執，不能重複送出。", "waiting_reconcile", {"operation_key": key})
            manager.result(request["run_id"], key, found)
            return found
        permit = manager.prepare(request, action)
        try:
            result = self.request("POST", "/actions", {"idempotency_key": key, "brand_url": request["brand_url"], "action": action, "payload_sha256": permit["payload_sha256"]})
        except UncertainWrite:
            manager.store.set_operation(key, "uncertain")
            found = self.request("GET", "/operations/" + quote(key, safe=""))
            if not found: raise
            result = found
        except WorkflowError:
            # An explicit rejection is distinct from an unknown write outcome.
            manager.store.set_operation(key, "rejected")
            manager.store.db.execute("UPDATE contact_reservations SET state='released' WHERE operation_key=?", (key,))
            raise
        manager.result(request["run_id"], key, result)
        return result
