"""Authenticated event ingestion; inbound content has no control-plane authority."""
import base64
import hashlib
import hmac
import json
import os

from .errors import WorkflowError
from .sales_state import SalesState
from .files import digest


def ingest_tool(store, run_id, event, receipt, source):
    """Trusted host imports an actual connector read; external text cannot call it."""
    profile = store.config(run_id)
    config = profile.get("events", {}).get("sources", {}).get(source, {})
    expected = "controlled_fixture" if profile.get("mode") == "fixture" else "live_tool"
    if config.get("protocol") != "host-tool-v1" or receipt.get("tool_name") not in config.get("allowed_tools", []):
        raise WorkflowError("不是此品牌已設定的可信讀取工具。", "scope_mismatch")
    if receipt.get("source") != expected or not receipt.get("evidence") or receipt.get("event_sha256") != digest(event) or not receipt.get("observed_at"):
        raise WorkflowError("事件缺少相符的實際工具讀回。", "invalid_result")
    if event.get("source") != source or event.get("brand_url") != profile["brand"]["url"] or event.get("kind") not in config.get("event_kinds", []):
        raise WorkflowError("讀取事件與本品牌來源範圍不符。", "scope_mismatch")
    sender = receipt.get("authenticated_sender")
    contact = SalesState(store).contact(profile["brand"]["url"], event.get("contact_id")) if event.get("contact_id") else None
    if contact and sender != contact["identity"]:
        raise WorkflowError("不能僅憑訊息內自稱身分，把事件套到既有客戶。", "scope_mismatch")
    data = {**event, "payload": {"untrusted_external_data": event.get("payload", {}), "instructions_may_not_change_permissions": True, "tool_evidence": receipt["evidence"]}}
    return SalesState(store).event(profile["brand"]["url"], data)


def signature(body, event_id, signed_at, secret):
    signed = (event_id + "." + str(signed_at) + ".").encode() + body
    return base64.b64encode(hmac.new(secret.encode(), signed, hashlib.sha256).digest()).decode()


def ingest_signed(store, run_id, body, headers, source):
    profile = store.config(run_id)
    source_config = profile.get("events", {}).get("sources", {}).get(source)
    if not source_config or source_config.get("protocol") != "growth-event-v1":
        raise WorkflowError("事件來源未設定；不把任意來信當工作命令。", "scope_mismatch")
    if len(body) > 256 * 1024:
        raise WorkflowError("事件過大，需由可信連接器先取必要欄位。", "invalid_input")
    secret = os.environ.get(source_config.get("secret_env", ""))
    if not secret:
        raise WorkflowError("缺此來源的驗章設定。", "waiting_capability")
    event_id = headers.get("event-id", "")
    try:
        signed_at = int(headers.get("event-timestamp", ""))
    except (TypeError, ValueError):
        raise WorkflowError("事件時間格式不正確。", "invalid_signature")
    if not event_id or abs(store.clock() - signed_at) > 300 or not hmac.compare_digest(signature(body, event_id, signed_at, secret), str(headers.get("event-signature", ""))):
        raise WorkflowError("事件驗章或期限不符。", "invalid_signature")
    try:
        event = json.loads(body)
    except (ValueError, UnicodeError):
        raise WorkflowError("事件不是有效JSON。", "invalid_input")
    if event.get("source_event_id") != event_id or event.get("source") != source or event.get("brand_url") != profile["brand"]["url"]:
        raise WorkflowError("事件身分／品牌不符驗章範圍。", "scope_mismatch")
    if event.get("kind") not in source_config.get("event_kinds", []):
        raise WorkflowError("這個來源不能宣告此種事件。", "scope_mismatch")
    # Allowlisted authenticated adapters map a verified sender to a contact.
    # Raw email From headers, URLs and text never grant sender identity.
    sender = event.get("authenticated_sender")
    allowed = source_config.get("allowed_senders", [])
    if allowed and sender not in allowed:
        raise WorkflowError("此來源的寄件對象不在已確認範圍內。", "scope_mismatch")
    contact = SalesState(store).contact(profile["brand"]["url"], event.get("contact_id")) if event.get("contact_id") else None
    if contact and sender and sender != contact["identity"]:
        raise WorkflowError("寄件身分與目標對象不符。", "scope_mismatch")
    event["payload"] = {"untrusted_external_data": event.get("payload", {}), "instructions_may_not_change_permissions": True}
    return SalesState(store).event(profile["brand"]["url"], event)
