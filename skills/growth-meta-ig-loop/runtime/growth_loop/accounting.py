"""Evidence-backed arithmetic. Unknown costs never become zero profit costs."""
import json
import math
from decimal import Decimal, InvalidOperation

from .errors import WorkflowError
from .files import canonical, digest, redact

COST_CATEGORIES = ("ads", "payment_processing", "platform", "commission", "software", "delivery", "labor", "other")


class Ledger:
    def __init__(self, store):
        self.store, self.db = store, store.db
        self.db.execute("""CREATE TABLE IF NOT EXISTS money_events(
          id TEXT PRIMARY KEY,brand TEXT NOT NULL,source TEXT NOT NULL,economic_id TEXT NOT NULL,
          kind TEXT NOT NULL,currency TEXT NOT NULL,occurred REAL NOT NULL,data TEXT NOT NULL,
          UNIQUE(brand,source,economic_id));""")

    def add(self, brand, row):
        if row.get("kind") not in ("payment", "refund", "cost", "reserve", "payout"):
            raise WorkflowError("不支援的金額事件種類。", "invalid_input")
        if not row.get("source") or not row.get("economic_id") or not row.get("evidence"):
            raise WorkflowError("金額需原始來源、經濟事件ID與核對依據。", "invalid_input")
        if row.get("verified") is not True:
            raise WorkflowError("待查金額不可灌入已核實帳本。", "invalid_input")
        amount = row.get("amount_minor")
        if amount is not None and (type(amount) is not int or amount < 0):
            raise WorkflowError("金額以非負整數最小貨幣單位保存；未知為null。", "invalid_input")
        currency = row.get("currency", "")
        if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
            raise WorkflowError("幣別需明確且使用三字大寫。", "invalid_input")
        if row["kind"] == "refund" and not row.get("payment_economic_id"):
            raise WorkflowError("退款需對應原付款，不重複扣。", "invalid_input")
        if row["kind"] == "cost" and row.get("category") not in COST_CATEGORIES:
            raise WorkflowError("成本需歸入明確類別。", "invalid_input")
        # economic_id is a canonical provider-prefixed ID (e.g. stripe:pi_x),
        # shared by webhook, CSV and connector views of the same transaction.
        existing = self.db.execute("SELECT id,data FROM money_events WHERE brand=? AND economic_id=?", (brand, row["economic_id"])).fetchone()
        if existing:
            old_row = json.loads(existing[1])
            fields = ("kind", "amount_minor", "currency", "payment_economic_id", "category", "external_customer", "test", "bank_available", "attribution", "customer_ref", "contract_ref", "contract_paid_in_full")
            if any(old_row.get(k) != row.get(k) for k in fields):
                raise WorkflowError("同一經濟事件的金額或分類不同，先核對，不重複入帳。", "payload_changed")
            return {"event_id": existing[0], "duplicate": True}
        key = digest({"brand": brand, "economic_id": row["economic_id"]})
        old = self.db.execute("SELECT data FROM money_events WHERE id=?", (key,)).fetchone()
        if old:
            if digest(json.loads(old[0])) != digest(redact(row)):
                raise WorkflowError("同一金額事件內容不同，需另提供更正事件。", "payload_changed")
            return {"event_id": key, "duplicate": True}
        stamp=row.get('occurred_at',self.store.clock())
        if type(stamp) not in (int,float) or not math.isfinite(stamp) or not 0<=stamp<=self.store.clock()+300:
            raise WorkflowError('金額事件需要有效的原始發生時間。','invalid_input')
        self.db.execute("INSERT INTO money_events VALUES(?,?,?,?,?,?,?,?)", (key, brand, row["source"], row["economic_id"], row["kind"], currency, stamp, canonical(redact(row))))
        return {"event_id": key, "duplicate": False}

    def summary(self, brand, currency, start=0, end=None, owner_share=None, reserve_complete=False, attribution=None, attribution_scope=None):
        end = self.store.clock() if end is None else end
        rows = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM money_events WHERE brand=? AND currency=? AND occurred>=? AND occurred<=?", (brand, currency, start, end))]
        def in_scope(row):
            scope=row.get('attribution',{})
            return not attribution_scope or isinstance(scope,dict) and all(scope.get(k)==v for k,v in attribution_scope.items())
        rows=[row for row in rows if in_scope(row)]
        payments = [r for r in rows if r["kind"] == "payment" and r.get("external_customer") is True and not r.get("test")]
        # A refund today can belong to an external payment from last month.
        all_payments = [json.loads(r[0]) for r in self.db.execute("SELECT data FROM money_events WHERE brand=? AND currency=? AND kind='payment'", (brand, currency))]
        all_payments=[row for row in all_payments if in_scope(row)]
        ids = {r["economic_id"] for r in all_payments if r.get("external_customer") is True and not r.get("test")}
        refunds = [r for r in rows if r["kind"] == "refund" and r["payment_economic_id"] in ids]
        known_parent_ids = {r["economic_id"] for r in all_payments}
        unmatched_refunds = [r["economic_id"] for r in rows if r["kind"] == "refund" and r["payment_economic_id"] not in known_parent_ids]
        def total(group):
            return None if any(r.get("amount_minor") is None for r in group) else sum(r["amount_minor"] for r in group)
        gross, returned = total(payments), None if unmatched_refunds else total(refunds)
        categories = {}
        for category in COST_CATEGORIES:
            costs = [r for r in rows if r["kind"] == "cost" and r.get("category") == category]
            # An explicit verified zero-cost record is different from no record.
            categories[category] = total(costs) if costs else None
        costs_complete = all(value is not None for value in categories.values())
        cost = sum(categories.values()) if costs_complete else None
        reserve = total([r for r in rows if r["kind"] == "reserve"]) if reserve_complete else None
        net = gross - returned - cost if all(x is not None for x in (gross, returned, cost)) else None
        share = None
        if owner_share is not None:
            try:
                share = Decimal(str(owner_share))
            except InvalidOperation:
                raise WorkflowError("可取得份額必須是已確認的比例。", "invalid_input")
            if not share.is_finite() or share < 0 or share > 1:
                raise WorkflowError("可取得份額需在0至1之間。", "invalid_input")
        available = int((Decimal(net - reserve) * share).quantize(Decimal("1"))) if net is not None and reserve is not None and share is not None else None
        payout = [r for r in rows if r["kind"] == "payout" and r.get("bank_available") is True]
        return {"brand_url": brand, "currency": currency, "period": {"start": start, "end": end}, "external_customer_payments": len(payments),
                "gross_receipts_minor": gross, "refunds_minor": returned, "costs_by_category_minor": categories,
                "all_costs_verified": costs_complete, "total_operating_cost_minor": cost, "net_after_operating_cost_minor": net,
                "future_delivery_reserve_minor": reserve, "owner_share": str(share) if share is not None else None,
                "owner_available_after_reserve_minor": available, "verified_bank_available_payout_minor": total(payout) if payout else None,
                "attribution": attribution or "unknown; source association does not prove incremental sales", "unknown_fields_are_not_zero": True,
                "unmatched_refund_ids": unmatched_refunds, "scope": "recorded verified events only; source completeness must be checked separately"}


def roi_scenarios(inputs):
    """S27: buyer-approved assumptions; never guaranteed realized ROI."""
    required = ("baseline_hours", "new_hours", "hourly_cost_minor", "price_minor")
    if any(inputs.get(k) is None for k in required):
        return {"estimated_net_benefit_minor": None, "status": "missing_buyer_baseline", "missing": [k for k in required if inputs.get(k) is None]}
    try:
        values = {k: Decimal(str(inputs[k])) for k in required}
    except InvalidOperation:
        raise WorkflowError("效益試算輸入不是數字。", "invalid_input")
    if any(not v.is_finite() or v < 0 for v in values.values()) or not inputs.get("period") or not inputs.get("currency") or not inputs.get("assumptions_source"):
        raise WorkflowError("試算需非負數值、同一期間／幣別與假設來源。", "invalid_input")
    savings = (values["baseline_hours"] - values["new_hours"]) * values["hourly_cost_minor"]
    return {"estimated_time_savings_hours": str(values["baseline_hours"] - values["new_hours"]), "estimated_labor_value_minor": int(savings),
            "estimated_net_benefit_minor": int(savings - values["price_minor"]), "period": inputs["period"], "currency": inputs["currency"],
            "assumptions_source": inputs["assumptions_source"], "status": "scenario_not_realized_profit", "guaranteed": False}
