from .errors import Waiting, WorkflowError
from .files import atomic_json, digest
from .policy import validate_profile


def next_variant(store, task, profile):
    previous = task["cycle"] - 1
    observations = {}
    for row in store.db.execute("SELECT kind,data FROM artifacts WHERE run_id=? AND cycle=? AND (kind LIKE 'observe.%' OR kind LIKE 'review.%')", (task["run_id"], previous)):
        import json
        observations[row[0]] = json.loads(row[1])
    request = store.artifact(task["run_id"], task["cycle"], "creative_request") or {"run_id": task["run_id"], "task_key": task["key"], "cycle": task["cycle"],
               "brand_url": profile["brand"]["url"], "facts": profile["brand"]["facts"],
               "previous_creative": store.artifact(task["run_id"], previous, "creative"), "evidence": observations}
    key = digest(request)
    store.artifact(task["run_id"], task["cycle"], "creative_request", request)
    result = store.artifact(task["run_id"], task["cycle"], "agent_creative")
    if result and result.get("request_sha256") == key:
        return result
    path = store.root / "creative-requests" / (key + ".json")
    atomic_json(path, {**request, "request_sha256": key, "instruction": "宿主Agent讀取真實觀測，提出可被推翻的原因，選一項變因改寫下一則；不得將互動當購買、補猜指標或增加產品能力。"})
    raise Waiting("下一輪改稿工作已交給宿主Agent，保留前輪數據與素材。", "waiting_agent", {"request_file": str(path)})


def record_creative_receipt(store, request, result):
    clean = {k: request[k] for k in ("run_id", "task_key", "cycle", "brand_url", "facts", "previous_creative", "evidence")}
    if digest(clean) != request.get("request_sha256"):
        raise WorkflowError("改稿輸入已改變。", "payload_changed")
    profile = store.config(request["run_id"])
    task = store.task(request["task_key"])
    if not task or task["cycle"] != request["cycle"] or task["run_id"] != request["run_id"] or profile["brand"]["url"] != request["brand_url"]:
        raise WorkflowError("改稿回執屬於其他流程。", "scope_mismatch")
    if not result.get("hypothesis") or not result.get("changed_variable"):
        raise WorkflowError("新稿需要說明依據與這輪改哪一件事。", "invalid_response")
    variant = result.get("variant", {})
    validate_profile({**profile, "creative": {"variants": [variant]}})
    old = request["previous_creative"]
    if variant.get("headline") == old.get("headline") and variant.get("body") == old.get("body") and variant.get("caption") == old.get("caption"):
        raise WorkflowError("下一輪不能只是同一份內容再發一次。", "invalid_response")
    store.artifact(request["run_id"], request["cycle"], "agent_creative", {**result, "request_sha256": request["request_sha256"]})
    if task["state"] == "waiting_agent":
        store.db.execute("UPDATE tasks SET state='ready',next_at=0 WHERE key=?", (task["key"],))
    store.event(request["run_id"], task["key"], "agent_creative_received", {"changed_variable": result["changed_variable"]})
    return {"recorded": True, "task_key": task["key"]}
