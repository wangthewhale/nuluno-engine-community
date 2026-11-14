from pathlib import Path
import re
from urllib.parse import urlparse

from .errors import Waiting, WorkflowError
from .files import atomic_json, digest, file_digest


class BrowserBridge:
    """Durable requests for the host agent's documented browser tools.

    This never scrapes cookies, extracts access tokens, or silently dispatches a
    browser command. The host agent performs the operation and records evidence.
    """
    def __init__(self, store, profile, policy):
        self.store, self.profile, self.policy = store, profile, policy

    def _request(self, task, action, payload):
        self.store.assert_lease(task)
        request = {"run_id": task["run_id"], "cycle": task["cycle"], "task_key": task["key"],
                   "action": action, "brand_url": self.profile["brand"]["url"], "payload": payload}
        key = digest(request)
        receipt = self.store.artifact(task["run_id"], task["cycle"], "browser_receipt_" + action)
        if receipt and receipt.get("request_sha256") == key:
            return receipt["result"]
        path = self.store.root / "browser-requests" / (key + ".json")
        atomic_json(path, {**request, "request_sha256": key, "receipt_status": "pending", "host_instruction": "使用宿主已支援的瀏覽器工具，依目前頁面操作；完成後讀回並保存可核對結果。"})
        raise Waiting("已建立可續接瀏覽器工作；宿主Agent應執行此工作並回填真實讀回，不需使用者手動重打。", "waiting_agent", {"request_file": str(path), "request_sha256": key})

    def account_ready(self, task):
        if task["data"]["channel"] == "meta_ads":
            account = self.profile.get("accounts", {}).get("ad_account_id")
            if not account:
                raise Waiting("請選定這個品牌要用的廣告帳號；其他素材與IG工作可繼續。", "waiting_owner", {"missing": "ad_account_id", "spending": False})
            return self._request(task, "identity_meta_ads", {"account_id": str(account).removeprefix("act_"), "page_id": self.profile.get("accounts", {}).get("page_id"), "currency": self.profile.get("budget", {}).get("currency")})
        return self._request(task, "identity_instagram", {"profile_url": self.profile.get("browser", {}).get("instagram_profile_url")})

    def publish_ads(self, task, creative):
        account = self.profile.get("accounts", {}).get("ad_account_id")
        self.policy.require("create_paused_ads", account)
        active = bool(self.profile.get("ads", {}).get("activate"))
        if active:
            self.policy.require("activate_ads", account, public=True, spending=True)
        budget = self.profile.get("budget", {})
        if not active and budget.get("total_minor") == 0 and budget.get("daily_minor") == 0:
            result = self._request(task, "create_paused_campaign", {"account_id": str(account).removeprefix("act_"), "name": "growth-" + task["run_id"] + " campaign", "objective": "OUTCOME_TRAFFIC", "special_ad_categories": self.profile.get("ads", {}).get("special_ad_categories", []), "status": "paused", "is_adset_budget_sharing_enabled": False, "spending_authorized": 0})
            delivery = "unpublished_draft_only" if result.get("status") == "UNPUBLISHED_DRAFT" else "paused_campaign_only"
            return {**result, "delivery": delivery, "limitation": "零支出驗收沒有啟用投放，也沒有授權正數預算；完整投放必須另核對品牌身分、預算、地區及平台必要聲明。"}
        if not budget.get("currency") or not budget.get("daily_minor") or not budget.get("minor_factor") or not self.profile.get("ads", {}).get("targeting"):
            raise Waiting("廣告草稿仍需核對幣別、每日金額與投放地區；未知值不自動猜。", "waiting_owner")
        return self._request(task, "publish_meta_ads", {"account_id": str(account).removeprefix("act_"), "page_id": self.profile.get("accounts", {}).get("page_id"), "creative": creative, "budget": budget, "targeting": self.profile["ads"]["targeting"], "expected_status": "ACTIVE" if active else "PAUSED", "name_marker": task["run_id"] + "-c" + str(task["cycle"])})

    def pause_ads(self, task, publication):
        self.policy.require("pause_ads", self.profile.get("accounts", {}).get("ad_account_id"), allow_expired_stop=True)
        return self._request(task, "pause_meta_ads", {"account_id": str(self.profile["accounts"]["ad_account_id"]).removeprefix("act_"), "campaign_id": publication["campaign_id"]})

    def publish_instagram(self, task, creative):
        url = self.profile.get("browser", {}).get("instagram_profile_url")
        self.policy.require("publish_instagram", public=True)
        if url not in self.policy.grant.get("browser_profiles", []):
            raise Waiting("這個IG頁面尚未列入本輪品牌的發布範圍。", "waiting_approval")
        return self._request(task, "publish_instagram", {"profile_url": url, "caption": creative["caption"],
                              "image_path": creative["asset"]["path"], "image_sha256": creative["asset"]["sha256"]})

    def observe(self, task, publications):
        if task["data"]["channel"] == "meta_ads":
            row = publications["meta_ads"]
            if row.get("delivery") in ("paused_campaign_only", "unpublished_draft_only"):
                return {"source": "stored_zero_spend_draft", "observed_at": self.store.clock(), "meta_ads": None, "instagram": None, "state": "zero_spend_draft_not_delivering"}
            result = self._request(task, "observe_meta_ads_%s" % task["data"]["index"], {"account_id": str(self.profile["accounts"]["ad_account_id"]).removeprefix("act_"), "campaign_id": row["campaign_id"], "ad_id": row["ad_id"]})
            return {"source": "live_browser", "observed_at": self.store.clock(), "meta_ads": result, "instagram": None}
        row = publications.get("instagram")
        result = self._request(task, "observe_instagram_%s" % task["data"]["index"], {"profile_url": self.profile.get("browser", {}).get("instagram_profile_url"), "post_url": row.get("readback", {}).get("permalink")})
        return {"source": "live_browser", "observed_at": self.store.clock(), "meta_ads": None, "instagram": result}


def record_browser_receipt(store, request, result):
    clean = {k: request[k] for k in ("run_id", "cycle", "task_key", "action", "brand_url", "payload")}
    if digest(clean) != request.get("request_sha256"):
        raise WorkflowError("瀏覽器工作內容已改變，拒絕不相符回執。", "payload_changed")
    config = store.config(request["run_id"])
    if config["brand"]["url"] != request["brand_url"]:
        raise WorkflowError("回執屬於其他品牌。", "scope_mismatch")
    task = store.task(request["task_key"])
    if not task or task["run_id"] != request["run_id"] or task["cycle"] != request["cycle"]:
        raise WorkflowError("找不到相符的待續任務。", "scope_mismatch")
    if not result.get("observed_at") or result.get("source") != "live_browser":
        raise WorkflowError("需要宿主實際瀏覽器讀回與時間。", "invalid_response")
    expected_profile = request["payload"].get("profile_url")
    if expected_profile and result.get("profile_url") != expected_profile:
        raise WorkflowError("瀏覽器身分或目的頁不符。", "scope_mismatch")
    if request["payload"].get("account_id") and result.get("account_id") != request["payload"]["account_id"]:
        raise WorkflowError("廣告帳號讀回不符。", "scope_mismatch")
    if request["action"] == "identity_meta_ads" and not result.get("management_controls_observed"):
        raise WorkflowError("未看到這個廣告帳號的管理入口。", "invalid_response")
    if request["action"] == "create_paused_campaign":
        valid_state = result.get("status") == "PAUSED" or (result.get("status") == "UNPUBLISHED_DRAFT" and result.get("configured_status") == "PAUSED")
        if not re.fullmatch(r"\d+", str(result.get("campaign_id", ""))) or not valid_state or result.get("name") != request["payload"]["name"]:
            raise WorkflowError("需要相符活動ID、名稱與實際PAUSED狀態。", "readback_mismatch")
        if not result.get("screenshot_path") or not Path(result["screenshot_path"]).is_file():
            raise WorkflowError("需要暫停活動讀回截圖。", "invalid_response")
    if request["action"] == "publish_meta_ads":
        for key in ("campaign_id", "adset_id", "ad_id"):
            if not re.fullmatch(r"\d+", str(result.get(key, ""))):
                raise WorkflowError("需要已讀回的廣告物件ID。", "invalid_response")
        if result.get("status") != request["payload"]["expected_status"] or result.get("budget") != request["payload"]["budget"]:
            raise WorkflowError("廣告狀態／費用讀回不符。", "readback_mismatch")
        if not result.get("screenshot_path") or not Path(result["screenshot_path"]).is_file():
            raise WorkflowError("需要廣告結果截圖。", "invalid_response")
    if request["action"] == "pause_meta_ads" and (not result.get("paused") or result.get("campaign_id") != request["payload"]["campaign_id"]):
        raise WorkflowError("沒有核對到此活動已暫停。", "readback_mismatch")
    if request["action"] == "identity_instagram" and not result.get("owner_controls_observed"):
        raise WorkflowError("沒有看到本人管理入口，不能當已登入該品牌。", "invalid_response")
    if request["action"] == "publish_instagram":
        parsed = urlparse(result.get("readback", {}).get("permalink", ""))
        if parsed.hostname not in ("www.instagram.com", "instagram.com") or not parsed.path.startswith(("/p/", "/reel/")):
            raise WorkflowError("需要實際貼文永久連結，不只送出成功畫面。", "invalid_response")
        if result.get("readback", {}).get("caption") != request["payload"]["caption"]:
            raise WorkflowError("實際貼文與這次工作文案不符。", "readback_mismatch")
        if not result.get("screenshot_path") or not Path(result["screenshot_path"]).is_file():
            raise WorkflowError("公開發布需保存實際結果截圖。", "invalid_response")
        if file_digest(request["payload"]["image_path"]) != request["payload"]["image_sha256"]:
            raise WorkflowError("發布前後素材檔案已改變。", "payload_changed")
    if request["action"].startswith("observe_instagram_"):
        if result.get("post_url") != request["payload"].get("post_url"):
            raise WorkflowError("觀測回執不屬於此貼文。", "scope_mismatch")
        for field in ("like_count", "comments_count"):
            if result.get(field) is not None and (type(result[field]) is not int or result[field] < 0):
                raise WorkflowError("無法讀到的成效應為null，不能猜成數字。", "invalid_response")
    store.artifact(request["run_id"], request["cycle"], "browser_receipt_" + request["action"], {"request_sha256": request["request_sha256"], "result": result})
    key = request["task_key"]
    row = store.task(key)
    if row and row["state"] == "waiting_agent":
        store.db.execute("UPDATE tasks SET state='ready',next_at=0 WHERE key=?", (key,))
    store.event(request["run_id"], key, "browser_receipt_recorded", {"source": "live_browser", "action": request["action"]})
    return {"recorded": True, "task_key": key}
