import re
from urllib.parse import urlparse

from .errors import Waiting, WorkflowError


def validate_profile(profile):
    if profile.get("mode", "prepare") not in ("prepare", "live", "fixture"):
        raise ValueError("mode must be prepare, live or fixture")
    if not profile.get("brand", {}).get("name"):
        raise ValueError("brand.name is required")
    url = urlparse(profile.get("brand", {}).get("url", ""))
    if url.scheme != "https" or not url.hostname:
        raise ValueError("brand.url must be a real HTTPS product URL")
    for field in ("ad_account_id", "page_id", "ig_user_id", "business_id"):
        value = profile.get("accounts", {}).get(field)
        if value is not None and not re.fullmatch(r"(?:act_)?\d+", str(value)):
            raise ValueError("Invalid or placeholder accounts." + field)
    budget = profile.get("budget", {})
    for field in ("daily_minor", "total_minor", "minor_factor"):
        value = budget.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Budget fields must be nonnegative integers, not floats")
    if budget.get("minor_factor") == 0:
        raise ValueError("minor_factor must be positive")
    if budget.get("daily_minor") and budget.get("total_minor") is not None:
        if budget["daily_minor"] > budget["total_minor"]:
            raise ValueError("Daily budget cannot exceed the run total")
    channels = profile.get("channels", ["meta_ads", "instagram"])
    if not channels or any(c not in ("meta_ads", "instagram") for c in channels):
        raise ValueError("This release supports meta_ads and instagram only")
    variants = profile.get("creative", {}).get("variants", [])
    if not variants:
        raise ValueError("At least one factual creative variant is required")
    for variant in variants:
        if not variant.get("headline") or not variant.get("body"):
            raise ValueError("Each creative needs headline and body")
        facts = profile.get("brand", {}).get("facts", {})
        if not variant.get("fact_ids") or any(i not in facts for i in variant["fact_ids"]):
            raise ValueError("Every creative must cite existing brand fact IDs")
    version = profile.get("graph", {}).get("version")
    if version is not None and not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Use a verified Graph API version, e.g. a vN.0 string")
    return profile


class Policy:
    def __init__(self, profile, grant, clock):
        self.profile, self.grant, self.clock = profile, grant or {}, clock

    def require(self, action, account_id=None, public=False, spending=False, allow_expired_stop=False):
        if self.profile.get("mode", "prepare") == "prepare":
            raise Waiting("已備妥具體操作；prepare模式不會對外寫入。", "waiting_approval", {"action": action})
        grant = self.grant
        if not grant.get("confirmed_by_user"):
            raise Waiting("缺少這個品牌與帳號的具體執行範圍。", "waiting_approval", {"action": action})
        if grant.get("brand_url") != self.profile["brand"]["url"]:
            raise WorkflowError("Authorization belongs to another product", "scope_mismatch")
        if grant.get("run_id") != self.profile.get("_run_id"):
            raise Waiting("這份執行範圍不屬於目前這一輪；不能重用到其他流程。", "waiting_approval")
        expiry_stop = allow_expired_stop and action == "pause_ads" and grant.get("pause_on_expiry")
        if float(grant.get("expires_at", 0)) <= self.clock() and not expiry_stop:
            raise Waiting("執行範圍已到期；進度保留。", "waiting_approval")
        if action not in grant.get("actions", []):
            raise Waiting("此動作不在已確認範圍內。", "waiting_approval", {"action": action})
        allowed = [str(x).removeprefix("act_") for x in grant.get("account_ids", [])]
        if account_id and str(account_id).removeprefix("act_") not in allowed:
            raise Waiting("尚未指定或授權這個帳號。", "waiting_approval", {"action": action})
        if public and not grant.get("allow_publication"):
            raise Waiting("公開發布尚未在這次驗收範圍內。", "waiting_approval")
        if spending:
            total = self.profile.get("budget", {}).get("total_minor")
            daily = self.profile.get("budget", {}).get("daily_minor")
            if not total or not daily or total > grant.get("total_minor", 0):
                raise Waiting("缺可用廣告預算或超過已確認上限。", "waiting_approval")
        return True

    def require_media_destination(self, url, token_env=None):
        host = urlparse(url).hostname
        if host not in self.grant.get("media_hosts", []):
            raise Waiting("媒體上傳目的地未在本輪設定範圍內。", "waiting_approval")
        if token_env and token_env in (self.profile.get("auth", {}).get("token_env", "META_ACCESS_TOKEN"), self.profile.get("auth", {}).get("app_secret_env", "META_APP_SECRET")):
            raise WorkflowError("Do not send Meta credentials to a media host", "scope_mismatch")
