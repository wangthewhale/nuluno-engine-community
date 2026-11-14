import base64
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlparse

from .errors import UncertainWrite, Waiting, WorkflowError
from .journal import Journal
from .media import verify_image


class MetaIGProvider:
    def __init__(self, client, profile, policy, store, uploader=None):
        self.client, self.profile = client, profile
        self.policy, self.store, self.uploader = policy, store, uploader
        self.evidence_source = "controlled_http_fixture" if profile.get("mode") == "fixture" else "live_graph"

    def discovery(self):
        identity = self.client.request("GET", "me", {"fields": "id,name"})
        accounts = self.client.all("me/adaccounts", {"fields": "id,name,currency,timezone_name,account_status"})
        pages = self.client.all("me/accounts", {"fields": "id,name,tasks,instagram_business_account{id,username}"})
        return {"identity": identity, "ad_accounts": accounts, "pages": pages, "source": self.evidence_source}

    def account_ready(self, task):
        channel = task.get("data", {}).get("channel", "meta_ads")
        if channel == "instagram":
            selected = self.profile.get("accounts", {})
            pages = self.client.all("me/accounts", {"fields": "id,name,tasks,instagram_business_account{id,username}"})
            page = next((p for p in pages if str(p["id"]) == str(selected.get("page_id"))), None)
            ig = page.get("instagram_business_account") if page else None
            if not ig or str(ig.get("id")) != str(selected.get("ig_user_id")):
                raise Waiting("需要選定本品牌可管理的IG專業帳號與粉專連接。", details={"pages": [{"id": p["id"], "name": p.get("name"), "instagram_business_account": p.get("instagram_business_account")} for p in pages]})
            return {"page": page, "instagram_ready": True, "source": self.evidence_source}
        found = self.discovery()
        selected = self.profile.get("accounts", {})
        account = selected.get("ad_account_id")
        if not account:
            if selected.get("create_ad_account"):
                created = self.create_ad_account(task, selected["create_ad_account"])
                account = created["id"]
                selected["ad_account_id"] = account
                self.store.update_config(task["run_id"], self.profile)
                found = self.discovery()
            else:
                raise Waiting("已找到可用資產；需要指定本品牌的廣告帳號，不自動挑第一個。", details={"accounts": [{"id": a["id"], "name": a.get("name")} for a in found["ad_accounts"]]})
        matched = [a for a in found["ad_accounts"] if str(a["id"]).removeprefix("act_") == str(account).removeprefix("act_")]
        if not matched:
            raise Waiting("指定廣告帳號不在目前授權可見資產中。")
        row = matched[0]
        if row.get("account_status") not in (None, 1):
            raise Waiting("廣告帳戶不是可用狀態；保留素材與原工作，不另開帳戶規避。", details={"account_status": row.get("account_status")})
        configured_currency = self.profile.get("budget", {}).get("currency")
        if configured_currency and row.get("currency") and configured_currency != row["currency"]:
            raise WorkflowError("Configured currency differs from the actual account", "currency_mismatch")
        page_id = selected.get("page_id")
        page = next((p for p in found["pages"] if str(p["id"]) == str(page_id)), None)
        if not page:
            raise Waiting("需要指定可管理且屬於此品牌的粉專。", details={"pages": [{"id": p["id"], "name": p.get("name")} for p in found["pages"]]})
        result = {"ad_account": row, "page": page, "source": found["source"]}
        if "instagram" in self.profile.get("channels", []):
            ig = page.get("instagram_business_account")
            if not ig or str(ig.get("id")) != str(selected.get("ig_user_id")):
                result["instagram_ready"] = False
                result["instagram_reason"] = "IG需正確的專業帳號與連接；缺少時可繼續其他工作。"
            else:
                result["instagram_ready"] = True
        return result

    def _find(self, path, name, fields="id,name"):
        records = self.client.all(path, {"fields": fields})
        matched = [r for r in records if r.get("name") == name]
        if len(matched) > 1:
            raise Waiting("找到重複平台物件，先核對而不是再建立。", "waiting_owner", {"name": name, "ids": [r["id"] for r in matched]})
        return matched[0] if matched else None

    def create_ad_account(self, task, spec):
        business = self.profile.get("accounts", {}).get("business_id")
        if not business or not spec.get("currency") or type(spec.get("timezone_id")) is not int:
            raise Waiting("開戶需要既有商務ID、真實幣別與時區ID；未確認值不亂填。")
        self.policy.require("create_ad_account", business)
        name = "Growth " + self.profile["brand"]["name"] + " " + task["run_id"]
        params = {"name": name, "currency": spec["currency"], "timezone_id": spec["timezone_id"]}
        if spec.get("end_advertiser"):
            params["end_advertiser"] = spec["end_advertiser"]
        journal = Journal(self.store, task)
        return journal.execute("create_ad_account", {"path": business + "/adaccount", "params": params},
                               lambda: self.client.request("POST", business + "/adaccount", params),
                               lambda: self._find(business + "/owned_ad_accounts", name))

    def _act(self):
        value = self.profile.get("accounts", {}).get("ad_account_id")
        if not value:
            raise Waiting("尚未指定品牌廣告帳號。")
        return "act_" + str(value).removeprefix("act_")

    def publish_ads(self, task, creative):
        account = self._act()
        self.policy.require("create_paused_ads", account)
        budget = self.profile.get("budget", {})
        if not budget.get("daily_minor") or not budget.get("total_minor") or not budget.get("minor_factor"):
            raise Waiting("素材與具體草稿已備妥；建立廣告組仍需確認幣別與有效預算。", "waiting_approval")
        page = self.profile.get("accounts", {}).get("page_id")
        targeting = self.profile.get("ads", {}).get("targeting")
        if not page or not targeting:
            raise Waiting("粉專與目標市場尚未指定，不猜廣告身份或受眾。")
        asset = verify_image(creative["asset"]["path"])
        journal = Journal(self.store, task)
        suffix = task["run_id"]
        prefix = "Growth " + self.profile["brand"]["name"] + " " + suffix

        image_request = {"asset_sha256": asset["sha256"], "account": account}
        encoded = base64.b64encode(Path(asset["path"]).read_bytes()).decode("ascii")
        images = journal.execute("image", image_request, lambda: self.client.request("POST", account + "/adimages", {"bytes": encoded}))
        values = list(images.get("images", {}).values())
        if not values or not values[0].get("hash"):
            raise WorkflowError("Image upload has no usable hash", "invalid_response")
        image_hash = values[0]["hash"]

        campaign_name = prefix + " campaign"
        campaign = self._find(account + "/campaigns", campaign_name)
        if not campaign:
            params = {"name": campaign_name, "objective": "OUTCOME_TRAFFIC", "special_ad_categories": self.profile.get("ads", {}).get("special_ad_categories", []), "status": "PAUSED", "spend_cap": budget["total_minor"]}
            campaign = journal.execute("campaign", params,
                                       lambda: self.client.request("POST", account + "/campaigns", params),
                                       lambda: self._find(account + "/campaigns", campaign_name))

        adset_name = prefix + " adset"
        adset = self._find(account + "/adsets", adset_name)
        if not adset:
            params = {"name": adset_name, "campaign_id": campaign["id"], "daily_budget": budget["daily_minor"], "billing_event": "IMPRESSIONS", "optimization_goal": "LINK_CLICKS", "destination_type": "WEBSITE", "targeting": targeting, "status": "PAUSED"}
            adset = journal.execute("adset", params,
                                    lambda: self.client.request("POST", account + "/adsets", params),
                                    lambda: self._find(account + "/adsets", adset_name))

        version = " c%02d" % task["cycle"]
        name = prefix + version + " creative"
        params = {"name": name, "object_story_spec": {"page_id": page, "link_data": {
            "image_hash": image_hash, "link": self.profile["brand"]["url"],
            "message": creative["caption"], "name": creative["headline"],
            "call_to_action": {"type": "LEARN_MORE", "value": {"link": self.profile["brand"]["url"]}},
        }}}
        adcreative = journal.execute("creative", params,
                                     lambda: self.client.request("POST", account + "/adcreatives", params),
                                     lambda: self._find(account + "/adcreatives", name))
        name = prefix + version + " ad"
        params = {"name": name, "adset_id": adset["id"], "creative": {"creative_id": adcreative["id"]}, "status": "PAUSED"}
        ad = journal.execute("ad", params,
                             lambda: self.client.request("POST", account + "/ads", params),
                             lambda: self._find(account + "/ads", name))
        state = {"campaign_id": campaign["id"], "adset_id": adset["id"], "ad_id": ad["id"], "creative_id": adcreative["id"], "asset_sha256": asset["sha256"], "delivery": "not_enabled"}
        campaign_readback = self.client.request("GET", campaign["id"], {"fields": "id,status,spend_cap"})
        adset_readback = self.client.request("GET", adset["id"], {"fields": "id,status,daily_budget,campaign_id"})
        if int(campaign_readback.get("spend_cap", -1)) != budget["total_minor"] or int(adset_readback.get("daily_budget", -1)) != budget["daily_minor"]:
            raise WorkflowError("Platform budget did not match the confirmed absolute limits", "budget_readback_mismatch")
        state["budget_readback"] = {"campaign": campaign_readback, "adset": adset_readback}
        if self.profile.get("ads", {}).get("activate"):
            self.policy.require("activate_ads", account, spending=True)
            for kind in ("campaign", "adset", "ad"):
                self._status(journal, "enable_" + kind, state[kind + "_id"], "ACTIVE")
            # Reuse one run-scoped ad set. Rotate ads inside that budget, instead
            # of silently starting another separately funded campaign each cycle.
            previous = self.store.artifact(task["run_id"], task["cycle"] - 1, "meta_ads")
            if previous and previous.get("ad_id") != ad["id"]:
                self._status(journal, "pause_previous", previous["ad_id"], "PAUSED")
            state["delivery"] = "enabled_pending_delivery"
        state["readback"] = self.client.request("GET", ad["id"], {"fields": "id,name,status,effective_status,adset_id,campaign_id,creative"})
        expected = "ACTIVE" if self.profile.get("ads", {}).get("activate") else "PAUSED"
        if state["readback"].get("status") != expected:
            raise WorkflowError("Ad configured status did not match readback", "readback_mismatch")
        return state

    def _status(self, journal, key, object_id, status):
        def reconcile():
            row = self.client.request("GET", object_id, {"fields": "id,status"})
            return {"success": True, "readback": row} if row.get("status") == status else None
        result = journal.execute(key, {"id": object_id, "status": status},
                                 lambda: self.client.request("POST", object_id, {"status": status}), reconcile)
        actual = self.client.request("GET", object_id, {"fields": "id,status"})
        if actual.get("status") != status:
            raise WorkflowError("Status change not confirmed by platform", "readback_mismatch")
        return result

    def publish_instagram(self, task, creative):
        ig = self.profile.get("accounts", {}).get("ig_user_id")
        if not ig:
            raise Waiting("IG專業帳號與授權尚未指定；廣告與素材工作可獨立繼續。")
        self.policy.require("publish_instagram", ig, public=True)
        journal = Journal(self.store, task)
        public_url = creative.get("public_image_url")
        if not public_url and self.uploader:
            self.policy.require_media_destination(self.uploader.url, self.uploader.token_env)
            uploaded = journal.execute("asset_host", {"sha256": creative["asset"]["sha256"]}, lambda: self.uploader.upload(creative["asset"]["path"]))
            public_url = uploaded["url"]
        parsed = urlparse(public_url or "")
        if not public_url or (parsed.scheme != "https" and self.profile.get("mode") != "fixture"):
            raise Waiting("IG需要可讀取的素材網址或已配置媒體上傳工具；本機檔不能假裝已上傳。", "waiting_capability")
        # Capture existing IDs before a public write. Caption matching by itself
        # can accidentally select an old identical post after a timeout.
        baseline_key = "ig_baseline"
        baseline = self.store.artifact(task["run_id"], task["cycle"], baseline_key)
        if baseline is None:
            previous = self.client.all(str(ig) + "/media", {"fields": "id"})
            baseline = {"ids": [x["id"] for x in previous], "captured_at": self.store.clock()}
            self.store.artifact(task["run_id"], task["cycle"], baseline_key, baseline)
        params = {"image_url": public_url, "caption": creative["caption"]}
        container = journal.execute("container", params, lambda: self.client.request("POST", str(ig) + "/media", params))
        status = self.client.request("GET", container["id"], {"fields": "id,status_code,status"})
        if status.get("status_code") not in ("FINISHED", "PUBLISHED"):
            if status.get("status_code") in ("ERROR", "EXPIRED"):
                raise WorkflowError("IG media container is not publishable", "invalid_asset", details=status)
            raise WorkflowError("IG media is still processing", "transient", retry_after=30)

        def reconcile():
            row = self.client.request("GET", container["id"], {"fields": "status_code"})
            if row.get("status_code") != "PUBLISHED":
                return None
            records = self.client.all(str(ig) + "/media", {"fields": "id,caption,permalink,timestamp"})
            start = baseline["captured_at"] - 5
            candidates = []
            for item in records:
                timestamp = item.get("timestamp")
                if timestamp:
                    try:
                        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                else:
                    continue
                if item["id"] not in baseline["ids"] and item.get("caption") == creative["caption"] and observed >= start:
                    candidates.append(item)
            if len(candidates) == 1:
                return candidates[0]
            return None

        published = journal.execute("publish", {"creation_id": container["id"]},
                                    lambda: self.client.request("POST", str(ig) + "/media_publish", {"creation_id": container["id"]}), reconcile)
        readback = self.client.request("GET", published["id"], {"fields": "id,caption,permalink,timestamp"})
        if readback.get("caption") != creative["caption"]:
            raise WorkflowError("IG post caption differs from the task", "readback_mismatch")
        return {"container_id": container["id"], "media_id": published["id"], "readback": readback}

    def observe(self, task, publications):
        result = {"observed_at": self.store.clock(), "source": self.evidence_source, "meta_ads": None, "instagram": None}
        ads = publications.get("meta_ads")
        if ads:
            rows = self.client.all(ads["ad_id"] + "/insights", {"fields": "ad_id,impressions,clicks,spend", "date_preset": "lifetime"})
            factor = self.profile.get("budget", {}).get("minor_factor")
            if not factor:
                raise Waiting("費用換算單位未核對，不能猜預算或幣別精度。")
            impressions = sum(int(r.get("impressions", 0)) for r in rows)
            clicks = sum(int(r.get("clicks", 0)) for r in rows)
            spend = sum((Decimal(str(r.get("spend", "0"))) for r in rows), Decimal(0))
            campaign_rows = self.client.all(ads["campaign_id"] + "/insights", {"fields": "spend", "date_preset": "lifetime"})
            campaign_spend = sum((Decimal(str(r.get("spend", "0"))) for r in campaign_rows), Decimal(0))
            status = self.client.request("GET", ads["ad_id"], {"fields": "id,status,effective_status"})
            result["meta_ads"] = {"impressions": impressions, "clicks": clicks, "spend_minor": int((spend * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "campaign_spend_minor": int((campaign_spend * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "currency": self.profile.get("budget", {}).get("currency"), "status": status, "purchases": None, "revenue": None, "costs_complete": False}
        ig = publications.get("instagram")
        if ig:
            result["instagram"] = self.client.request("GET", ig["media_id"], {"fields": "id,permalink,like_count,comments_count"})
        return result

    def pause_ads(self, task, publication):
        self.policy.require("pause_ads", self._act(), allow_expired_stop=True)
        journal = Journal(self.store, task)
        self._status(journal, "pause_campaign", publication["campaign_id"], "PAUSED")
        return {"paused": True, "campaign_id": publication["campaign_id"]}
