"""Plans, usage limits and the billing adapter (M8).

Plans are code (small, reviewed, versioned). Usage is counted per organization per month.
Billing: Stripe Checkout + webhook, called over HTTPS with httpx (no SDK). It is NOT
operational until STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET and STRIPE_PRICE_* are set —
`configured()` is False and the endpoints say so. Never tested against live Stripe here.
Admins can always set a plan manually (invoices handled outside the app).
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Organization, SavedSearch, UsageCounter

PLANS: dict[str, dict] = {
    "free": {"label": "Free", "saved_searches": 3, "alerts": False, "exports_per_month": 5, "export_max_rows": 500},
    "pro": {"label": "Pro", "saved_searches": 25, "alerts": True, "exports_per_month": 100, "export_max_rows": 10_000},
    "business": {"label": "Business", "saved_searches": 200, "alerts": True, "exports_per_month": 1_000, "export_max_rows": 100_000},
}


class LimitExceeded(Exception):
    pass


class BillingNotConfigured(Exception):
    pass


def plan_of(org: Organization) -> dict:
    return PLANS.get(org.plan, PLANS["free"])


def period(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m")


def usage(db: Session, org: Organization, metric: str, now: datetime | None = None) -> int:
    row = db.scalar(
        select(UsageCounter).where(UsageCounter.organization_id == org.id, UsageCounter.metric == metric, UsageCounter.period == period(now))
    )
    return row.count if row else 0


def add_usage(db: Session, org: Organization, metric: str, n: int = 1, now: datetime | None = None) -> None:
    p = period(now)
    row = db.scalar(
        select(UsageCounter)
        .where(UsageCounter.organization_id == org.id, UsageCounter.metric == metric, UsageCounter.period == p)
        .with_for_update()
    )
    if row is None:
        db.add(UsageCounter(organization_id=org.id, metric=metric, period=p, count=n))
    else:
        row.count += n


def check_export(db: Session, org: Organization, rows: int) -> int:
    """→ allowed row count. Raises if the monthly export quota is used up."""
    plan = plan_of(org)
    if usage(db, org, "exports") >= plan["exports_per_month"]:
        raise LimitExceeded(f"{plan['label']} plan allows {plan['exports_per_month']} exports per month")
    return min(rows, plan["export_max_rows"])


def check_saved_search(db: Session, org: Organization, alerts: bool) -> None:
    plan = plan_of(org)
    count = len(db.scalars(select(SavedSearch.id).where(SavedSearch.organization_id == org.id)).all())
    if count >= plan["saved_searches"]:
        raise LimitExceeded(f"{plan['label']} plan allows {plan['saved_searches']} saved searches")
    if alerts and not plan["alerts"]:
        raise LimitExceeded(f"alerts are not included in the {plan['label']} plan")


def summary(db: Session, org: Organization) -> dict:
    plan = plan_of(org)
    return {
        "plan": org.plan,
        "limits": plan,
        "usage": {"exports": usage(db, org, "exports"), "export_rows": usage(db, org, "export_rows"),
                  "saved_searches": len(db.scalars(select(SavedSearch.id).where(SavedSearch.organization_id == org.id)).all())},
        "billing_configured": configured(),
        "subscription_status": org.subscription_status,
    }  # fmt: skip


# ---------- Stripe adapter ----------


def configured() -> bool:
    return bool(settings.stripe_secret_key and settings.stripe_webhook_secret and settings.stripe_price_pro)


def _price(plan: str) -> str:
    return {"pro": settings.stripe_price_pro, "business": settings.stripe_price_business}.get(plan, "")


def checkout_url(org: Organization, plan: str, client: httpx.Client | None = None) -> str:
    if not configured():
        raise BillingNotConfigured("billing is not configured (set STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_PRO/BUSINESS)")
    price = _price(plan)
    if not price:
        raise BillingNotConfigured(f"no Stripe price configured for plan {plan!r}")
    form = {
        "mode": "subscription",
        "line_items[0][price]": price,
        "line_items[0][quantity]": "1",
        "client_reference_id": str(org.id),
        "metadata[plan]": plan,
        "success_url": f"{settings.public_web_url}/account?billing=success",
        "cancel_url": f"{settings.public_web_url}/account?billing=cancelled",
    }
    if org.billing_customer_id:
        form["customer"] = org.billing_customer_id
    c = client or httpx.Client(timeout=20)
    r = c.post("https://api.stripe.com/v1/checkout/sessions", data=form, auth=(settings.stripe_secret_key, ""))
    r.raise_for_status()
    return r.json()["url"]


def verify_webhook(payload: bytes, header: str, secret: str, now: float | None = None, tolerance: int = 300) -> dict:
    """Stripe-Signature: t=<ts>,v1=<hex hmac_sha256(secret, f'{t}.{payload}')>. Raises ValueError if invalid."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    sigs = [v for k, v in (p.split("=", 1) for p in header.split(",") if "=" in p) if k == "v1"]
    ts = parts.get("t", "")
    if not ts.isdigit() or not sigs:
        raise ValueError("malformed signature header")
    if abs((now or time.time()) - int(ts)) > tolerance:
        raise ValueError("signature timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in sigs):
        raise ValueError("signature mismatch")
    return json.loads(payload)


def apply_event(db: Session, event: dict) -> str:
    """Idempotent: applying the same event twice leaves the same state."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed":
        org = db.get(Organization, _uuid(obj.get("client_reference_id")))
        plan = (obj.get("metadata") or {}).get("plan")
        if org and plan in PLANS:
            org.plan, org.billing_customer_id, org.subscription_status = plan, obj.get("customer"), "active"
            db.commit()
            return "plan_activated"
    elif kind in ("customer.subscription.deleted", "customer.subscription.updated"):
        org = db.scalar(select(Organization).where(Organization.billing_customer_id == obj.get("customer")))
        if org:
            status = obj.get("status") or ("canceled" if kind.endswith("deleted") else None)
            org.subscription_status = status
            if status in ("canceled", "unpaid", "incomplete_expired"):
                org.plan = "free"
            db.commit()
            return "subscription_updated"
    return "ignored"


def _uuid(v):
    import uuid

    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None
