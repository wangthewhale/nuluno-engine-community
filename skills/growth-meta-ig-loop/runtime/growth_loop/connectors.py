import re

from .errors import Waiting, WorkflowError


def unwrap(result):
    if isinstance(result, dict) and result.get("structuredContent") is not None:
        return result["structuredContent"]
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        import json
        for item in result["content"]:
            if item.get("type") == "text":
                try:
                    return json.loads(item["text"])
                except (ValueError, KeyError):
                    pass
    return result


def verified_windsor_rows(result, required_fields=()):
    """Some connector plan errors arrive as status=done and fake string rows."""
    result = unwrap(result)
    rows = result.get("data") if isinstance(result, dict) else None
    if rows is None and isinstance(result, dict):
        nested = result.get("result")
        rows = nested.get("data") if isinstance(nested, dict) else nested
    if not isinstance(rows, list):
        raise WorkflowError("連接工具沒有回傳可核對資料列。", "invalid_response")
    patterns = ("not your real numbers", "reads are paused", "free plan includes", "upgrade at", "subscription required")
    for row in rows:
        if not isinstance(row, dict):
            raise WorkflowError("連接工具資料列格式不正確。", "invalid_response")
        for value in row.values():
            if isinstance(value, str) and any(p in value.lower() for p in patterns):
                raise Waiting("連接工具因方案／帳號數限制暫停讀取；不是實際成效。保留工作，改已授權官方連接或由本人選擇連接方案。", "waiting_capability", {"classification": "connector_quota", "automatic_upgrade": False, "automatic_disconnect": False})
        if any(key not in row for key in required_fields):
            raise WorkflowError("連接工具缺本步必要欄位。", "invalid_response")
        if row.get("account_id") is not None and not re.fullmatch(r"(?:act_)?\d+", str(row["account_id"])):
            raise WorkflowError("帳號欄位不是有效識別；不能當作真實資料。", "invalid_response")
    return rows
