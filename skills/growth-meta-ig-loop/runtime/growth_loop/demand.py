"""Find public, current requests; a public signal is never a purchase receipt."""
import re
import math
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from .errors import WorkflowError
from .files import digest

SIGNALS = {
    "recommendation": [r"any\s+(?:tool\s+)?recommendations?", r"can anyone recommend", r"有[沒有]*人.*推薦", r"求推薦", r"有什麼.*工具", r"哪[個款].*好用"],
    "alternative": [r"alternative\s+to", r"switch(?:ing)?\s+from", r"替代(?:品|方案|工具)", r"有沒有.*取代"],
    "service_request": [r"looking for (?:a|an|someone|help)", r"need (?:a|an|someone|help)", r"想找.*(?:工具|服務|幫忙)", r"徵求.*(?:服務|廠商)", r"求.*(?:琴譜|工具|服務)"],
    "pain": [r"struggling (?:to|with)", r"how (?:do|can) (?:i|we)", r"請問.*怎麼", r"有沒有.*辦法", r"找不到.*(?:工具|琴譜|方法)"]}
PROMOTIONAL = [r"(?:i|we) (?:built|launched|created)\b", r"sign up (?:for|now)", r"我(?:們)?(?:做了|推出|開發了)"]
RESOLVED = [r"(?:already|finally) (?:found|solved|fixed)", r"(?:已經|已)(?:解決|找到|買到)", r"不用了"]


def public_url(value):
    p = urlparse(value or "")
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise WorkflowError("需求來源必須是可核對的公開HTTPS網址。", "invalid_input")
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", urlencode(query), ""))


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise WorkflowError("來源時間不是有效數字。", "invalid_input")
        return float(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed.timestamp()
    except (ValueError, TypeError):
        raise WorkflowError("來源時間需包含時區；未知日期不能當成今天。", "invalid_input")


def search_plan(brand, buyer_terms, competitors=(), communities=()):
    if not buyer_terms or any(not isinstance(t, str) or not t.strip() for t in buyer_terms):
        raise WorkflowError("先用產品實際能解決的工作建立搜尋詞。", "invalid_input")
    queries = []
    for term in buyer_terms[:20]:
        for pattern in ('"recommend" "{term}"', '"looking for" "{term}"', '"求推薦" "{term}"', '"找不到" "{term}"'):
            queries.append({"query": pattern.format(term=term.replace('"', '')), "reason": "尋找明確需求原文，不抓泛泛流量"})
    for term in competitors[:10]:
        queries.append({"query": '"alternative to" "' + term.replace('"', '') + '"', "reason": "找明確想換方案的人；仍需核對產品適配"})
    return {"brand_url": brand["url"], "queries": queries, "communities": list(communities), "read_context_before_reply": True,
            "default_contact_route": "original_public_thread", "cold_email_enabled": False,
            "refresh_interval_seconds": 43200, "target_candidates": 20,
            "empty_result_rule": "如實交付不足20則，不補造需求或把舊文說成新需求。"}


def qualify(posts, brand, clock, max_age_days=14, history=()):
    if type(max_age_days) not in (int, float) or not math.isfinite(max_age_days) or max_age_days <= 0:
        raise WorkflowError("需求有效天數必須大於0。", "invalid_input")
    facts = brand.get("facts", {})
    known = set(history)
    seen_urls, seen_people, accepted, rejected = set(), set(), [], []
    for post in posts:
        reason = None
        try:
            url = public_url(post.get("url"))
            published = timestamp(post.get("published_at"))
        except WorkflowError as exc:
            rejected.append({"url": post.get("url"), "reason": str(exc)})
            continue
        text = post.get("text", "")
        person = (post.get("platform", "unknown").lower(), str(post.get("author_id", "")).strip().lower())
        key = digest({"brand": brand["url"], "url": url})
        matches = [intent for intent, expressions in SIGNALS.items() if any(re.search(p, text, re.I) for p in expressions)]
        fit = post.get("fit", {})
        quote = post.get("evidence_quote", "")
        fact_ids = fit.get("fact_ids", [])
        age = clock() - published
        if url in seen_urls or key in known:
            reason = "duplicate_or_already_processed"
        elif not person[1]:
            reason = "author_identity_missing"
        elif person in seen_people:
            reason = "same_person_has_another_current_task"
        elif age < -300 or age > max_age_days * 86400:
            reason = "outside_current_observation_window"
        elif post.get("resolved") or any(re.search(p, text, re.I) for p in RESOLVED):
            reason = "need_already_resolved"
        elif post.get("context_type") in ("employment", "recruitment") and not fit.get("existing_recruitment_offer"):
            reason = "recruiting_request_not_this_product_need"
        elif post.get("seller_promotion") or any(re.search(p, text, re.I) for p in PROMOTIONAL):
            reason = "promotion_not_a_buyer_request"
        elif not matches or post.get("explicit_request_verified") is not True:
            reason = "request_not_verified_in_context"
        elif not quote or len(quote.strip()) < 8 or quote not in text:
            reason = "need_quote_missing_or_not_in_original"
        elif not fact_ids or any(i not in facts for i in fact_ids) or not fit.get("reason"):
            reason = "fit_not_supported_by_existing_product_facts"
        elif fit.get("requires_product_change") or fit.get("unsupported_requirement"):
            reason = "requires_capability_the_product_does_not_have"
        elif post.get("community_rules", {}).get("promotion_allowed") is not True:
            reason = "community_rules_do_not_allow_this_promotion_or_unknown"
        if reason:
            rejected.append({"url": url, "author_id": post.get("author_id"), "reason": reason})
            continue
        seen_urls.add(url); seen_people.add(person)
        # Score is for queue priority only. It is not purchase probability.
        score = 50 + (20 if "recommendation" in matches or "service_request" in matches else 10) + max(0, int(15 * (1 - age / (max_age_days * 86400))))
        accepted.append({"signal_id": key, "url": url, "platform": post["platform"], "author_id": post["author_id"],
                         "published_at": published, "observed_at": clock(), "need_quote": quote, "original_text": text,
                         "intent": matches, "fact_ids": fact_ids, "fit_reason": fit["reason"], "priority_score": score,
                         "contact_route": "original_public_thread", "community_rules": post["community_rules"],
                         "purchase_intent": "explicit_need_not_confirmed_purchase", "status": "qualified_for_relevant_reply"})
    accepted.sort(key=lambda row: (-row["priority_score"], -row["published_at"]))
    return {"brand_url": brand["url"], "qualified": accepted, "excluded": rejected,
            "counts": {"read": len(posts), "qualified": len(accepted), "excluded": len(rejected)},
            "no_cold_email": True, "purchases": None, "revenue": None}


def reply_brief(signal, brand):
    return {"target_url": signal["url"], "author_id": signal["author_id"], "need_quote": signal["need_quote"],
            "permitted_facts": {i: brand["facts"][i] for i in signal["fact_ids"]}, "brand_url": brand["url"],
            "instructions": ["先回答對方當前問題，再說產品能幫哪一小段。", "透明揭露與品牌的關係，不假裝路人心得。", "尊重社群規則；只回相關原串，不挖Email、不群發同文。", "不把這個人的需求當成他同意訂閱或一定會購買。"],
            "requires_fresh_context_check": True, "delivery_record": None}


def build_in_public_brief(brand, verified_facts, lessons, interval_days=7):
    if not lessons:
        raise WorkflowError("故事要有實際遇到的事與可說明的教訓。", "invalid_input")
    for row in verified_facts:
        if not row.get("source") or row.get("verification") != "verified":
            raise WorkflowError("未核實的收入或成效數字不能變成自己的故事。", "invalid_input")
    return {"brand_url": brand["url"], "posts_per_interval": 2, "interval_days": interval_days,
            "structure": ["一個具體事件或失敗", "有來源的真實數字；沒有就省略數字", "這次學到什麼、適用限制", "相關時自然揭露產品與現有入口"],
            "verified_facts": verified_facts, "lessons": lessons, "next_skills": ["S10", "S11", "S14", "S33", "S34"],
            "attachment_revenue_claims_reusable_as_own_results": False}
