"""Brand-scoped contacts, suppression, follow-ups and immutable inbound events."""
import json
import math
import re
from urllib.parse import urlparse

from .errors import Waiting, WorkflowError
from .files import canonical, digest, redact


class SalesState:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS contacts(
          id TEXT PRIMARY KEY, brand TEXT NOT NULL, channel TEXT NOT NULL,
          identity TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL,
          UNIQUE(brand,channel,identity));
        CREATE TABLE IF NOT EXISTS followups(
          id TEXT PRIMARY KEY, brand TEXT NOT NULL, run_id TEXT NOT NULL,
          contact_id TEXT NOT NULL, purpose TEXT NOT NULL, due REAL NOT NULL,
          state TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sales_events(
          id TEXT PRIMARY KEY, brand TEXT NOT NULL, source TEXT NOT NULL,
          source_event_id TEXT NOT NULL, contact_id TEXT, kind TEXT NOT NULL,
          occurred REAL NOT NULL, data TEXT NOT NULL,
          UNIQUE(brand,source,source_event_id));
        CREATE TABLE IF NOT EXISTS contact_reservations(
          contact_id TEXT PRIMARY KEY, brand TEXT NOT NULL, operation_key TEXT NOT NULL,
          expires REAL NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_sends(
          operation_key TEXT PRIMARY KEY, brand TEXT NOT NULL, contact_id TEXT NOT NULL,
          channel TEXT NOT NULL, purpose TEXT NOT NULL, sent_at REAL NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS contact_reply_claims(
          brand TEXT NOT NULL, contact_id TEXT NOT NULL, event_id TEXT NOT NULL,
          purpose TEXT NOT NULL, operation_key TEXT NOT NULL,
          PRIMARY KEY(brand,contact_id,event_id,purpose));
        CREATE TABLE IF NOT EXISTS sales_documents(
          id TEXT PRIMARY KEY, brand TEXT NOT NULL, run_id TEXT NOT NULL,
          kind TEXT NOT NULL, data TEXT NOT NULL, updated REAL NOT NULL);
        """)

    def upsert_contact(self, brand, row, trusted_consent=False):
        channel, identity = row.get("channel"), str(row.get("identity", "")).strip()
        if not channel or not identity:
            raise WorkflowError("名單需要渠道與可核對身分，不猜Email。", "invalid_input")
        if channel == "email":
            identity = identity.lower()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", identity):
                raise WorkflowError("郵件地址格式不正確。", "invalid_input")
        contact_id = digest({"brand": brand, "channel": channel, "identity": identity})[:32]
        old = self.contact(brand, contact_id)
        protected = {"status", "marketing_consent", "suppression_reason", "last_contact_at"}
        data = {**(old or {}), **{k: v for k, v in row.items() if k not in protected}, "id": contact_id,
                "brand_url": brand, "channel": channel, "identity": identity}
        data.update({"status": (old or {}).get("status", "new"), "marketing_consent": (old or {}).get("marketing_consent", "unknown"), "suppression_reason": (old or {}).get("suppression_reason"), "last_contact_at": (old or {}).get("last_contact_at")})
        if trusted_consent and row.get("consent_evidence") and row.get("marketing_consent") in ("opted_in", "opted_out"):
            data["marketing_consent"] = row["marketing_consent"]
            data["consent_evidence"] = row["consent_evidence"]
        self.db.execute("INSERT INTO contacts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data,updated=excluded.updated", (contact_id, brand, channel, identity, canonical(redact(data)), self.store.clock()))
        return data

    def contact(self, brand, contact_id):
        row = self.db.execute("SELECT data FROM contacts WHERE id=? AND brand=?", (contact_id, brand)).fetchone()
        return json.loads(row[0]) if row else None

    def contacts(self, brand):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM contacts WHERE brand=? ORDER BY updated,id", (brand,))]

    def event(self, brand, event):
        """Caller must authenticate the source before entering this data plane."""
        kinds = {"reply", "purchase", "unsubscribe", "hard_bounce", "complaint", "declined", "need_resolved", "resource_requested", "opt_in_verified", "meeting_completed", "trial_result", "terms_confirmed", "listing_approved", "delivery_confirmed"}
        if event.get("kind") not in kinds or not event.get("source") or not event.get("source_event_id"):
            raise WorkflowError("事件需要受支援種類、已驗證來源與原始ID。", "invalid_input")
        contact_id = event.get("contact_id")
        contact = self.contact(brand, contact_id) if contact_id else None
        if contact_id and not contact:
            raise WorkflowError("事件不屬於此品牌的對象。", "scope_mismatch")
        key = digest({"brand": brand, "source": event["source"], "id": event["source_event_id"]})
        body = {"kind": event["kind"], "contact_id": contact_id, "source": event["source"], "source_event_id": event["source_event_id"], "occurred_at": event.get("occurred_at", self.store.clock()), "payload": event.get("payload", {})}
        if type(body["occurred_at"]) not in (int, float) or not math.isfinite(body["occurred_at"]) or body["occurred_at"] > self.store.clock() + 300:
            raise WorkflowError("事件時間需是可核對的有效時間，不能來自未來。", "invalid_input")
        old = self.db.execute("SELECT data FROM sales_events WHERE id=?", (key,)).fetchone()
        if old:
            if digest(json.loads(old[0])) != digest(redact(body)):
                raise WorkflowError("同一來源事件ID帶入不同內容。", "payload_changed")
            return {"event_id": key, "duplicate": True, "kind": event["kind"]}
        with self.store.transaction():
            self.db.execute("INSERT INTO sales_events VALUES(?,?,?,?,?,?,?,?)", (key, brand, event["source"], event["source_event_id"], contact_id, event["kind"], body["occurred_at"], canonical(redact(body))))
            if contact:
                kind = event["kind"]
                status = {"reply": "replied", "purchase": "purchased", "unsubscribe": "unsubscribed", "hard_bounce": "blocked", "complaint": "blocked", "declined": "declined", "need_resolved": "resolved"}.get(kind)
                if status:
                    contact["status"] = status
                    if kind == "unsubscribe":
                        contact["marketing_consent"] = "opted_out"
                    if kind in ("hard_bounce", "complaint"):
                        contact["suppression_reason"] = kind
                    self.db.execute("UPDATE followups SET state='cancelled' WHERE brand=? AND contact_id=? AND state='pending'", (brand, contact_id))
                    self.db.execute("UPDATE contact_reservations SET state='cancelled' WHERE brand=? AND contact_id=? AND state='reserved'", (brand, contact_id))
                if kind == "opt_in_verified":
                    contact["marketing_consent"] = "opted_in"
                    contact["consent_evidence"] = {"event_id": key, "source": event["source"], "at": body["occurred_at"]}
                contact["last_event_id"] = key
                self.db.execute("UPDATE contacts SET data=?,updated=? WHERE id=? AND brand=?", (canonical(contact), self.store.clock(), contact_id, brand))
            self._wake_matching(brand, event, key)
        return {"event_id": key, "duplicate": False, "kind": event["kind"]}

    def _wake_matching(self, brand, event, key):
        rows = self.db.execute("SELECT key,run_id,outcome FROM tasks WHERE state='waiting_event'").fetchall()
        for row in rows:
            if self.store.config(row["run_id"])["brand"]["url"] != brand:
                continue
            outcome = json.loads(row["outcome"] or "{}")
            wait = outcome.get("wait_for", {})
            if event.get("occurred_at", 0) >= wait.get("after", self.store.config(row["run_id"]).get("_created_at", 0)) and wait.get("kind") == event["kind"] and (not wait.get("contact_id") or wait["contact_id"] == event.get("contact_id")):
                # Incoming text is evidence, never a new instruction/permission.
                self.db.execute("UPDATE tasks SET state='ready',next_at=0,outcome=? WHERE key=?", (canonical({**outcome, "resumed_by_event": key}), row["key"]))

    def event_record(self, brand, event_id):
        row = self.db.execute("SELECT data FROM sales_events WHERE id=? AND brand=?", (event_id, brand)).fetchone()
        return json.loads(row[0]) if row else None

    def followup(self, run_id, contact_id, purpose, due_at, data):
        brand = self.store.config(run_id)["brand"]["url"]
        contact = self.contact(brand, contact_id)
        if not contact:
            raise WorkflowError("跟進對象不存在於此品牌。", "scope_mismatch")
        if contact.get("suppression_reason") or contact["status"] in ("unsubscribed", "purchased", "replied", "declined", "resolved"):
            return {"scheduled": False, "reason": "current_contact_state_stops_old_followup"}
        key = digest({"brand": brand, "contact": contact_id, "purpose": purpose, "trigger": data.get("trigger_id", run_id)})
        old = self.db.execute("SELECT data FROM followups WHERE id=?", (key,)).fetchone()
        if old and digest(json.loads(old[0])) != digest(data):
            raise WorkflowError("同一跟進任務不能悄悄改稿或改對象。", "payload_changed")
        self.db.execute("INSERT OR IGNORE INTO followups VALUES(?,?,?,?,?,?,?,?)", (key, brand, run_id, contact_id, purpose, due_at, "pending", canonical(redact(data))))
        return {"scheduled": True, "followup_id": key, "due_at": due_at}

    def due_followups(self, brand):
        return [dict(r) for r in self.db.execute("SELECT * FROM followups WHERE brand=? AND state='pending' AND due<=? ORDER BY due", (brand, self.store.clock()))]

    def can_contact(self, brand, contact_id, purpose, source_event_id=None, require_opt_in=False):
        contact = self.contact(brand, contact_id)
        if not contact:
            raise WorkflowError("沒有可核對的聯絡對象。", "scope_mismatch")
        if contact.get("suppression_reason"):
            raise Waiting("此對象已停止聯絡或退件，不送出。", "waiting_contact", {"reason": contact["suppression_reason"]})
        event = self.event_record(brand, source_event_id) if source_event_id else None
        fresh_requested_reply = event and event.get("contact_id") == contact_id and ((purpose == "reply" and event["kind"] == "reply") or (purpose == "resource_delivery" and event["kind"] == "resource_requested"))
        latest = self.event_record(brand, contact.get("last_event_id")) if contact.get("last_event_id") else None
        if latest and latest["kind"] in ("unsubscribe", "purchase", "hard_bounce", "complaint", "declined", "need_resolved") and event and event["occurred_at"] <= latest["occurred_at"]:
            fresh_requested_reply = False
        if (contact["status"] in ("unsubscribed", "purchased", "replied", "declined", "resolved") or contact.get("marketing_consent") == "opted_out") and not fresh_requested_reply:
            raise Waiting("對方狀態已更新，取消舊行銷跟進。", "waiting_contact")
        if require_opt_in and contact.get("marketing_consent") != "opted_in" and not fresh_requested_reply:
            raise Waiting("缺這種後續行銷的同意證據。", "waiting_contact")
        return contact

    def reserve_contact(self, brand, contact_id, operation_key, purpose, max_daily=20, cooldown_seconds=86400, source_event_id=None, require_opt_in=False, context_url=None):
        contact = self.can_contact(brand, contact_id, purpose, source_event_id, require_opt_in)
        now = self.store.clock()
        if type(max_daily) is not int or max_daily < 1 or type(cooldown_seconds) not in (int, float) or not math.isfinite(cooldown_seconds) or cooldown_seconds < 0:
            raise WorkflowError("聯絡數量與間隔設定不正確。", "invalid_input")
        if purpose in ("reply", "resource_delivery"):
            event = self.event_record(brand, source_event_id) if source_event_id else None
            expected = "reply" if purpose == "reply" else "resource_requested"
            if not event or event.get("contact_id") != contact_id or event["kind"] != expected:
                raise WorkflowError("回覆或交付例外必須對應這個人實際發出的事件。", "scope_mismatch")
            claim = self.db.execute("SELECT operation_key FROM contact_reply_claims WHERE brand=? AND contact_id=? AND event_id=? AND purpose=?", (brand, contact_id, source_event_id, purpose)).fetchone()
            if claim and claim[0] != operation_key:
                previous = self.store.operation(claim[0])
                if not previous or previous["state"] != "rejected":
                    raise Waiting("這次詢問已由另一個動作接手；先核對，不能改稿再回一次。", "waiting_reconcile")
        claim_id = source_event_id if purpose in ("reply", "resource_delivery") else None
        if purpose == "public_reply":
            if not context_url:
                raise WorkflowError("原串回覆需要明確原串網址。", "invalid_input")
            claim_id = "thread:" + context_url
            claim = self.db.execute("SELECT operation_key FROM contact_reply_claims WHERE brand=? AND contact_id=? AND event_id=? AND purpose=?", (brand, contact_id, claim_id, purpose)).fetchone()
            if claim and claim[0] != operation_key:
                previous = self.store.operation(claim[0])
                if not previous or previous["state"] != "rejected":
                    raise Waiting("這個需求原串已接手或已回覆；有新提問再依新事件回答。", "waiting_reconcile" if previous and previous["state"] in ("in_flight", "uncertain") else "waiting_contact")
        old = self.db.execute("SELECT * FROM contact_reservations WHERE contact_id=?", (contact_id,)).fetchone()
        if old and old["operation_key"] != operation_key and old["state"] == "reserved" and old["expires"] > now:
            raise Waiting("另一條流程已保留這個人的聯絡工作。", "waiting_contact")
        if old and old["operation_key"] != operation_key and old["state"] == "reserved":
            operation = self.store.operation(old["operation_key"])
            if operation and operation["state"] in ("in_flight", "uncertain"):
                raise Waiting("前次聯絡結果仍不明；保留到完成對帳，不能因鎖到期重發。", "waiting_reconcile")
        last = self.db.execute("SELECT max(sent_at) FROM contact_sends WHERE brand=? AND contact_id=?", (brand, contact_id)).fetchone()[0]
        if last is not None and now - last < cooldown_seconds and purpose not in ("reply", "resource_delivery"):
            raise Waiting("尚未超過這位對象的聯絡間隔。", "waiting_contact")
        # A rolling 24-hour cap avoids midnight/timezone resets allowing a burst.
        start = now - 86400
        count = self.db.execute("SELECT count(*) FROM contact_sends WHERE brand=? AND sent_at>=?", (brand, start)).fetchone()[0]
        reserved = self.db.execute("SELECT count(*) FROM contact_reservations WHERE brand=? AND state='reserved' AND expires>? AND operation_key<>?", (brand, now, operation_key)).fetchone()[0]
        if count + reserved >= max_daily:
            earliest = self.db.execute("SELECT min(sent_at) FROM contact_sends WHERE brand=? AND sent_at>=?", (brand, start)).fetchone()[0]
            raise Waiting("最近24小時的聯絡數量已滿，保留到下個窗口。", "waiting_contact", {"resume_after": (earliest + 86401) if earliest is not None else now + 120})
        if claim_id:
            self.db.execute("INSERT OR REPLACE INTO contact_reply_claims VALUES(?,?,?,?,?)", (brand, contact_id, claim_id, purpose, operation_key))
        self.db.execute("INSERT OR REPLACE INTO contact_reservations VALUES(?,?,?,?,?)", (contact_id, brand, operation_key, now + 120, "reserved"))
        return contact

    def record_send(self, brand, operation_key, action, receipt):
        contact_id = action.get("target", {}).get("contact_id")
        if not contact_id:
            return
        contact = self.contact(brand, contact_id)
        if not contact:
            raise WorkflowError("送出回執對象不符。", "scope_mismatch")
        self.db.execute("INSERT OR IGNORE INTO contact_sends VALUES(?,?,?,?,?,?,?)", (operation_key, brand, contact_id, action["channel"], action.get("purpose", "outreach"), self.store.clock(), canonical(redact(receipt))))
        self.db.execute("UPDATE contact_reservations SET state='sent' WHERE operation_key=?", (operation_key,))
        if action.get("followup_id"):
            self.db.execute("UPDATE followups SET state='done' WHERE id=? AND brand=? AND contact_id=? AND state='pending'", (action["followup_id"], brand, contact_id))
        contact["last_contact_at"] = self.store.clock()
        if contact["status"] == "new":
            contact["status"] = "contacted"
        self.db.execute("UPDATE contacts SET data=?,updated=? WHERE id=? AND brand=?", (canonical(contact), self.store.clock(), contact_id, brand))

    def document(self, run_id, kind, document, document_id=None):
        brand = self.store.config(run_id)["brand"]["url"]
        key = document_id or digest({"brand": brand, "run": run_id, "kind": kind, "data": document})
        self.db.execute("INSERT OR REPLACE INTO sales_documents VALUES(?,?,?,?,?,?)", (key, brand, run_id, kind, canonical(redact(document)), self.store.clock()))
        return {"document_id": key, "kind": kind}
