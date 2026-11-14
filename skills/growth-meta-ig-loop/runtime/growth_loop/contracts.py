"""Concrete hand-off contracts for all 34 work skills, beyond prose files."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .errors import WorkflowError
from .files import file_digest, safe_path
from .media import verify_image
from .resources import reference

FIELDS = {
 "S01": {"facts": dict, "offer": dict},
 "S02": {"segments": list, "hypotheses": list},
 "S03": {"examples": list, "test_angles": list},
 "S04": {"plan": dict},
 "S05": {"accounts": list},
 "S06": {"keyword_groups": list, "negative_terms": list, "topics": list},
 "S07": {"findings": list, "datasets": list},
 "S08": {"leads": list, "excluded": list},
 "S09": {"company": dict, "roles": list, "open_questions": list},
 "S10": {"content_batch": list},
 "S11": {"copy": list},
 "S12": {"assets": list},
 "S13": {"assets": list, "demonstration_kind": str},
 "S14": {"publications": list},
 "S15": {"listing": dict, "requirements": list},
 "S16": {"capture": dict, "deliveries": list, "leads": list},
 "S17": {"followups": list, "stopped": list},
 "S18": {"messages": list},
 "S19": {"answers": list, "open_questions": list, "buyer_state": str},
 "S20": {"program": dict, "participation": list},
 "S21": {"event": dict, "registrations": list, "attendance": list, "questions": list},
 "S22": {"testimonials": list, "referrals": list},
 "S23": {"partners": list, "terms": list, "deliverables": list},
 "S24": {"partner_kits": list, "tracking": list, "commission_records": list},
 "S25": {"questions": list, "buyer_answers": list, "fit": dict},
 "S26": {"demo": dict, "buyer_feedback": list, "limitations": list},
 "S27": {"proposal": dict, "roi_inputs": dict},
 "S28": {"pilot": dict, "milestones": list, "results": list},
 "S29": {"procurement": dict, "documents": list, "decisions_required": list},
 "S30": {"ad_plan": dict, "creative_tests": list, "stop_rules": list},
 "S31": {"ad_objects": list, "delivery_state": str, "changes": list},
 "S32": {"decision": str, "reasons": list, "change_orders": list, "creative_brief": dict},
 "S33": {"observations": list, "money_events": list, "limitations": list},
 "S34": {"facts": list, "hypotheses": list, "next_actions": list, "next_iteration": dict},
}

CAPABILITIES = {
 "S01": ["web.read"], "S02": ["web.research"], "S03": ["web.research"], "S04": ["local.plan"],
 "S05": ["accounts.inspect"], "S06": ["web.search"], "S07": ["research.read", "local.calculate"],
 "S08": ["web.search", "crm.read"], "S09": ["web.research", "crm.read"], "S10": ["local.plan"],
 "S11": ["agent.write"], "S12": ["media.image"], "S13": ["media.video"], "S14": ["content.publish"],
 "S15": ["listing.manage"], "S16": ["capture.manage", "resource.deliver"], "S17": ["crm.followup"],
 "S18": ["message.send"], "S19": ["message.read", "agent.answer"], "S20": ["community.manage"],
 "S21": ["events.manage"], "S22": ["crm.read", "permission.record"], "S23": ["partners.manage"],
 "S24": ["partners.enable"], "S25": ["conversation.read"], "S26": ["product.demonstrate_existing"],
 "S27": ["local.calculate", "agent.propose_existing_offer"], "S28": ["pilot.monitor_existing"],
 "S29": ["procurement.coordinate"], "S30": ["ads.plan"], "S31": ["ads.manage"],
 "S32": ["ads.read"], "S33": ["analytics.read", "payments.read"], "S34": ["local.review"],
}

ALLOWED_ACTIONS = {
 "S05": {"create_marketing_account"}, "S14": {"publish_content", "update_content"},
 "S15": {"publish_listing", "update_listing"}, "S16": {"create_capture", "deliver_resource"},
 "S18": {"send_message", "reply_message", "deliver_resource"}, "S20": {"publish_content", "update_community"},
 "S21": {"create_event", "update_event"}, "S23": {"send_message", "deliver_resource"},
 "S24": {"deliver_resource", "send_message"}, "S26": {"deliver_resource"},
 "S27": {"deliver_resource"}, "S28": {"deliver_resource"}, "S29": {"deliver_resource"},
 "S31": {"create_paused_ads", "activate_ads", "pause_ads", "update_ads"},
}


def blueprint():
    return json.loads(reference("blueprint.json").read_text(encoding="utf-8"))


def definition(skill_id):
    return next(s for s in blueprint()["skills"] if s["id"] == skill_id)


def required_action(skill_id, action):
    if skill_id == "S18": return {"send_message", "reply_message", "deliver_resource"}
    if skill_id == "S14": return {"publish_content", "update_content"}
    if skill_id == "S15" and re.search("提交|維護|更新", action): return {"publish_listing", "update_listing"}
    if skill_id == "S16" and "建立外部入口" in action: return {"create_capture"}
    if skill_id == "S21" and ("定主題" in action or "建立" in action): return {"create_event", "update_event"}
    if skill_id == "S20" and "發布任務" in action: return {"publish_content", "update_community"}
    if skill_id == "S31":
        if "暫停" in action and "建立" not in action and "上傳" not in action: return {"pause_ads"}
        if "啟用" in action: return {"activate_ads"}
        if "建立" in action or "上傳" in action: return {"create_paused_ads"}
        if "調整" in action or "更新" in action: return {"update_ads", "pause_ads"}
    return set()


def _proof(value, mode):
    if not value or not isinstance(value, str):
        return False
    p = urlparse(value)
    return p.scheme == "https" and bool(p.hostname) or (mode == "fixture" and p.scheme == "http" and p.hostname in ("127.0.0.1", "localhost")) or value.startswith("event:")


def validate_result(skill_id, data, profile, store, evidence=()):
    def fail(message):
        raise WorkflowError(skill_id + "：" + message, "invalid_result")
    for key, expected in FIELDS[skill_id].items():
        if key not in data or not isinstance(data[key], expected): fail("缺少正確格式的交付欄位 " + key)
    mode = profile.get("mode", "prepare")
    facts = profile["brand"].get("facts", {})
    if skill_id == "S01":
        for key, row in data["facts"].items():
            if not isinstance(row, dict) or not row.get("text") or not _proof(row.get("source"), mode): fail("產品事實需要可核對來源。")
        if data["offer"].get("existing_url") != profile["brand"]["url"]: fail("產品入口與本品牌不同。")
    if skill_id in ("S02", "S10", "S11", "S19"):
        rows = data.get({"S02": "segments", "S10": "content_batch", "S11": "copy", "S19": "answers"}[skill_id], [])
        for row in rows:
            ids = row.get("fact_ids", [])
            if not ids or any(i not in facts for i in ids): fail("內容必須對應此品牌已知事實，不能增加產品功能。")
            if row.get("cta_url") and row["cta_url"] not in [profile["brand"]["url"]] + profile["brand"].get("existing_urls", []): fail("行動入口不是已核對的現有入口。")
    if skill_id == "S05":
        for row in data["accounts"]:
            if not row.get("channel") or not row.get("account_ref") or row.get("state") not in ("connected", "missing", "expired", "waiting_owner"): fail("帳號狀態或身分不明。")
            if row["state"] == "connected" and not row.get("evidence"): fail("已連接帳號需本人管理證據。")
    if skill_id == "S06":
        for row in data["keyword_groups"]:
            if not row.get("terms") or row.get("intent") not in ("information", "comparison", "transaction", "brand") or not _proof(row.get("source"), mode): fail("關鍵字需要意圖、詞與來源。")
            if row.get("volume") is not None and not row.get("measured_volume_evidence"): fail("沒有搜尋量來源就填null。")
    if skill_id == "S07":
        for row in data["findings"]:
            if not row.get("claim") or not row.get("sources") or not all(_proof(s, mode) for s in row["sources"]): fail("研究主張缺來源。")
            if row.get("causal") and not row.get("causal_design_evidence"): fail("沒有對照設計不能宣稱因果。")
    if skill_id == "S08":
        for row in data["leads"]:
            if not row.get("identity") or not row.get("channel") or not _proof(row.get("source_url"), mode) or not row.get("reason"): fail("候選需要身分、渠道、原始來源與接觸理由。")
    if skill_id == "S09":
        for row in data["roles"]:
            if row.get("person") and not row.get("evidence"): fail("人物與角色需要來源。")
            if row.get("authority") not in ("confirmed", "hypothesis", "unknown"): fail("角色需區分確認與推測。")
    if skill_id in ("S12", "S13"):
        if not data["assets"]: fail("沒有實際媒體檔。")
        for asset in data["assets"]:
            try:
                path = safe_path(store.root, asset.get("path", ""))
            except ValueError:
                fail("先把授權素材存進本品牌工作區，不能交付任意本機路徑。")
            if not path.is_file() or asset.get("sha256") != file_digest(path): fail("素材不存在或雜湊不符。")
            if skill_id == "S12": verify_image(path)
            if skill_id == "S13":
                from .video import verify_video
                verify_video(path)
        if skill_id == "S13" and data["demonstration_kind"] not in ("graphic", "recorded_product", "authorized_existing_recording"): fail("需區分圖文與實際示範。")
    if skill_id == "S16":
        capture = data["capture"]
        if capture.get("state") == "ready" and (not _proof(capture.get("url"), mode) or not capture.get("end_to_end_evidence")): fail("表單存在不等於交付測通。")
        for row in data["leads"]:
            if row.get("marketing_consent") == "opted_in" and not row.get("consent_evidence"): fail("下載一次不等於同意所有行銷。")
    if skill_id == "S17":
        for row in data["followups"]:
            if not row.get("contact_id") or not row.get("purpose") or type(row.get("due_at")) not in (int, float): fail("跟進需要對象、目的與確切時間。")
    if skill_id == "S18":
        for row in data["messages"]:
            if not row.get("text") or not row.get("contact_id"): fail("訊息需要個別對象與實際全文。")
    if skill_id == "S21":
        event = data["event"]
        if event.get("state") == "completed" and (not event.get("completion_evidence") or not event.get("host_method")): fail("活動未實際舉辦不能標完成。")
    if skill_id == "S22":
        for row in data["testimonials"]:
            if not row.get("quote") or not row.get("source") or not row.get("permission_scope"): fail("心得要有真實原話、來源與分享範圍。")
    if skill_id == "S23":
        for row in data["terms"]:
            if row.get("state") == "confirmed" and not row.get("confirmation_evidence"): fail("合作條件未獲對方確認。")
    if skill_id == "S24":
        for row in data["tracking"]:
            if not row.get("source") or row.get("native_affiliate_feature_created"): fail("沿用既有推薦方式，不偷偷修改產品。")
    if skill_id == "S25":
        for row in data["buyer_answers"]:
            if not row.get("answer") or not row.get("source"): fail("不能自己填買方答案。")
        if data["fit"].get("status") not in ("fit", "not_fit", "unknown"): fail("適配需明確區分待查。")
    if skill_id == "S26" and data["demo"].get("state") == "shown" and not data["demo"].get("evidence"): fail("沒有展示證據。")
    if skill_id == "S28":
        for row in data["results"]:
            if row.get("status") not in ("met", "not_met", "unknown") or not row.get("criterion"): fail("試辦需逐項對照事先條件。")
            if row["status"] != "unknown" and not row.get("evidence"): fail("試辦結果不能補猜。")
    if skill_id == "S29" and data["procurement"].get("contract_accepted_by_agent"): fail("這個執行器不代替有權人接受新法律承諾。")
    if skill_id == "S30":
        amount = data["ad_plan"].get("total_minor")
        cap = profile.get("budget", {}).get("total_minor", 0)
        if amount is not None and (type(amount) is not int or amount < 0 or amount > cap): fail("廣告規劃超出使用者設定總額。")
    if skill_id == "S31" and data["delivery_state"] not in ("draft", "paused", "pending_review", "delivering", "rejected", "no_delivery"):
        fail("廣告配送狀態不明。")
    if skill_id == "S32" and data["decision"] not in ("hold", "repair", "pause", "adjust", "new_creative", "stop"):
        fail("需要可執行的廣告判斷。")
    if skill_id == "S34":
        next_plan = data["next_iteration"]
        if next_plan.get("decision") not in ("iterate", "wait", "stop"): fail("必須真的指定下一輪或停止理由。")
        if not next_plan.get("reason"): fail("下一輪需要理由。")
        if next_plan["decision"] in ("iterate", "wait") and (type(next_plan.get("delay_seconds")) not in (int, float) or next_plan["delay_seconds"] < 0): fail("等待時間不正確。")
    return data
