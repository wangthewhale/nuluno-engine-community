from .errors import WorkflowError


def review(channel, observation, profile, cycle):
    rules = profile.get("review", {})
    if channel == "meta_ads":
        if observation.get("state") == "zero_spend_draft_not_delivering":
            return {"decision": "complete_zero_spend", "reason": "已完成這輪零支出草稿驗收，沒有啟用配送；不產生假成效或重複排無用觀測。"}
        data = observation.get("meta_ads")
        if not data:
            return {"decision": "hold", "reason": "尚無可核對廣告資料；不把缺資料當0成效。"}
        total = profile.get("budget", {}).get("total_minor")
        cumulative = data.get("campaign_spend_minor", data.get("spend_minor"))
        if total and cumulative is not None and cumulative >= total:
            return {"decision": "pause", "reason": "觸及已設定的整輪廣告費用上限。"}
        if data.get("status", {}).get("effective_status") in ("DISAPPROVED", "WITH_ISSUES"):
            return {"decision": "repair_delivery", "reason": "先修投放／審核問題，不把無配送判為素材差。"}
        impressions = data.get("impressions")
        clicks = data.get("clicks")
        if impressions is None or clicks is None:
            return {"decision": "hold", "reason": "必要指標未到齊，維持並排下一次讀取。"}
        if impressions < rules.get("min_impressions", 1000):
            return {"decision": "hold", "reason": "資料量尚未達本輪設定的觀察門檻。"}
        elapsed = observation.get("age_seconds", 0)
        if elapsed < rules.get("min_age_seconds", 86400):
            return {"decision": "hold", "reason": "觀察窗未到，不因短期波動改設定。"}
        rate = clicks / impressions if impressions else None
        return {"decision": "new_creative", "reason": "已達這輪設定的觀察條件，新增一項可比較素材測試；不宣稱必然改善。", "ctr": rate, "purchases": data.get("purchases"), "revenue": data.get("revenue"), "business_profit_verified": False}
    data = observation.get("instagram")
    if not data:
        return {"decision": "hold", "reason": "尚未取得IG貼文的可用資料。"}
    if observation.get("age_seconds", 0) < rules.get("organic_min_age_seconds", 86400):
        return {"decision": "hold", "reason": "保留內容與觀察窗口，尚不重發。"}
    return {"decision": "new_creative", "reason": "依目前互動製作下一篇不同切角內容；互動不當購買證據。", "business_profit_verified": False}
