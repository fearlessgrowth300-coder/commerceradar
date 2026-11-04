"""Accounts, workspace (saved searches, alerts, exports), billing and admin routes (M8)."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, admin, current
from app.config import settings
from app.db import get_db
from app.models import Organization, SavedSearch, User
from app.services import accounts, benchmark, billing, domains, metrics, suppression, workspace

public = APIRouter(prefix="/api/v1")
router = APIRouter(prefix="/api/v1")


class Register(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)
    name: str | None = Field(None, max_length=200)
    organization: str | None = Field(None, max_length=200)


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class Me(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None
    is_admin: bool
    organization: dict[str, Any]
    role: str


@public.post("/auth/register", status_code=201)
def register(body: Register, db: Session = Depends(get_db)):
    if not settings.allow_registration:
        raise HTTPException(403, "registration is closed")
    try:
        accounts.register(db, body.email, body.password, body.name, body.organization)
        token = accounts.login(db, body.email, body.password)
    except accounts.AuthError as e:
        raise HTTPException(422, str(e)) from e
    return {"token": token}


@public.post("/auth/login")
def login(body: Login, db: Session = Depends(get_db)):
    try:
        return {"token": accounts.login(db, body.email, body.password)}
    except accounts.AuthError as e:
        raise HTTPException(401, str(e)) from e


@router.post("/auth/logout", status_code=204)
def logout(ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    accounts.logout(db, ctx.token)


@router.get("/auth/me", response_model=Me)
def me(ctx: Ctx = Depends(current)):
    u = ctx.user
    return {"id": u.id, "email": u.email, "name": u.name, "is_admin": u.is_admin, "role": ctx.role,
            "organization": {"id": ctx.org.id, "name": ctx.org.name, "plan": ctx.org.plan}}  # fmt: skip


@router.get("/account")
def account(ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    return billing.summary(db, ctx.org) | {"plans": billing.PLANS}


# ---------- saved searches + alerts ----------


class SearchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: dict[str, Any] = {}
    alerts_enabled: bool = False


class SearchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    filters: dict[str, Any]
    alerts_enabled: bool
    last_alert_check_at: datetime | None
    created_at: datetime


@router.get("/searches", response_model=list[SearchOut])
def list_searches(ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    return db.scalars(select(SavedSearch).where(SavedSearch.organization_id == ctx.org.id).order_by(SavedSearch.created_at.desc())).all()


@router.post("/searches", response_model=SearchOut, status_code=201)
def create_search(body: SearchIn, ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    try:
        return workspace.create_search(db, ctx.org, ctx.user, body.name, body.filters, body.alerts_enabled)
    except billing.LimitExceeded as e:
        raise HTTPException(402, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, f"invalid filters: {e}") from e


@router.delete("/searches/{search_id}", status_code=204)
def delete_search(search_id: uuid.UUID, ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    try:
        workspace.delete_search(db, ctx.org, search_id)
    except workspace.NotFound as e:
        raise HTTPException(404, "saved search not found") from e


@router.get("/alerts")
def list_alerts(unread: bool = False, ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    return [
        {"id": a.id, "store_id": a.store_id, "domain": dom, "store_name": name, "search": search,
         "created_at": a.created_at, "read_at": a.read_at}
        for a, search, dom, name in workspace.list_alerts(db, ctx.org, unread)
    ]  # fmt: skip


@router.post("/alerts/read")
def read_alerts(ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    return {"marked": workspace.mark_alerts_read(db, ctx.org)}


# ---------- exports ----------


@router.get("/exports/stores.csv")
def export_stores(request: Request, ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    try:
        text, rows, truncated = workspace.export_csv(db, ctx.org, dict(request.query_params))
    except billing.LimitExceeded as e:
        raise HTTPException(402, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, f"invalid filters: {e}") from e
    return Response(
        text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="commerceradar-stores.csv"',
                 "X-Rows": str(rows), "X-Truncated-By-Plan": "1" if truncated else "0"},
    )  # fmt: skip


# ---------- billing ----------


class Checkout(BaseModel):
    plan: str = Field(pattern="^(pro|business)$")


@router.post("/billing/checkout")
def checkout(body: Checkout, ctx: Ctx = Depends(current), db: Session = Depends(get_db)):
    if ctx.role != "owner":
        raise HTTPException(403, "only the organization owner can change the plan")
    try:
        return {"url": billing.checkout_url(ctx.org, body.plan)}
    except billing.BillingNotConfigured as e:
        raise HTTPException(501, str(e)) from e


@public.post("/billing/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    if not billing.configured():
        raise HTTPException(501, "billing is not configured")
    payload = await request.body()
    try:
        event = billing.verify_webhook(payload, request.headers.get("stripe-signature", ""), settings.stripe_webhook_secret)
    except ValueError as e:
        raise HTTPException(400, f"invalid signature: {e}") from e
    return {"result": billing.apply_event(db, event)}


# ---------- admin ----------


class PlanIn(BaseModel):
    plan: str


class UserPatch(BaseModel):
    is_active: bool | None = None
    is_admin: bool | None = None


class SuppressIn(BaseModel):
    domain: str = Field(min_length=1, max_length=2000)
    reason: str = Field("opt-out", max_length=300)


@router.get("/admin/overview", dependencies=[Depends(admin)])
def admin_overview(db: Session = Depends(get_db)):
    return workspace.admin_overview(db)


@router.put("/admin/organizations/{org_id}/plan", dependencies=[Depends(admin)])
def set_plan(org_id: uuid.UUID, body: PlanIn, db: Session = Depends(get_db)):
    if body.plan not in billing.PLANS:
        raise HTTPException(422, f"plan must be one of {', '.join(billing.PLANS)}")
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(404, "organization not found")
    org.plan = body.plan
    db.commit()
    return {"id": org.id, "plan": org.plan}


@router.patch("/admin/users/{user_id}")
def patch_user(user_id: uuid.UUID, body: UserPatch, ctx: Ctx = Depends(admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "user not found")
    if user.id == ctx.user.id and (body.is_admin is False or body.is_active is False):
        raise HTTPException(409, "you cannot remove your own admin access")
    for k in ("is_active", "is_admin"):
        if getattr(body, k) is not None:
            setattr(user, k, getattr(body, k))
    db.commit()
    return {"id": user.id, "is_active": user.is_active, "is_admin": user.is_admin}


@router.get("/admin/suppressions", dependencies=[Depends(admin)])
def suppressions(db: Session = Depends(get_db)):
    return [{"domain": d.normalized_domain, "reason": d.suppression_reason, "suppressed_at": d.suppressed_at}
            for d in suppression.listed(db)]  # fmt: skip


@router.get("/admin/suppressions/events", dependencies=[Depends(admin)])
def suppression_events(db: Session = Depends(get_db)):
    return [{"domain": e.domain, "action": e.action, "reason": e.reason, "actor_user_id": e.actor_user_id,
             "subdomains": e.subdomains, "created_at": e.created_at} for e in suppression.events(db)]  # fmt: skip


@router.post("/admin/suppressions", status_code=201)
def suppress(body: SuppressIn, ctx: Ctx = Depends(admin), db: Session = Depends(get_db)):
    try:
        host = domains.normalize_domain(body.domain)
    except domains.InvalidDomain as e:
        raise HTTPException(422, str(e)) from e
    d = suppression.suppress(db, host, body.reason, ctx.user.id)
    return {"domain": d.normalized_domain, "suppressed_at": d.suppressed_at}


@router.delete("/admin/suppressions/{domain}", status_code=204)
def unsuppress(domain: str, ctx: Ctx = Depends(admin), db: Session = Depends(get_db)):
    try:
        host = domains.normalize_domain(domain)
    except domains.InvalidDomain as e:
        raise HTTPException(404, "not suppressed") from e
    if suppression.unsuppress(db, host, ctx.user.id) is None:
        raise HTTPException(404, "not suppressed")



@router.get("/admin/metrics", dependencies=[Depends(admin)])
def admin_metrics(db: Session = Depends(get_db)):
    """Measured throughput, durations, source yield, storage and queue depth (last 24 h)."""
    return metrics.snapshot(db)



# ---------- blind labelling (M11) ----------


class LabelIn(BaseModel):
    domain: str = Field(max_length=253)
    verdict: str = Field(pattern="^(store|not_store|inactive_storefront|inaccessible|uncertain)$")
    platform: str | None = Field(None, max_length=30)
    notes: str = Field("", max_length=500)


@router.get("/admin/benchmark/next", dependencies=[Depends(admin)])
def benchmark_next(db: Session = Depends(get_db)):
    """One domain to label. Deliberately WITHOUT our verdict, stratum or source (blind)."""
    item = benchmark.next_item(db)
    return {"item": {"domain": item.domain, "url": f"https://{item.domain}/"} if item else None,
            "progress": benchmark.progress(db), "platforms": list(benchmark.PLATFORMS)}  # fmt: skip


@router.post("/admin/benchmark/label")
def benchmark_label(body: LabelIn, ctx: Ctx = Depends(admin), db: Session = Depends(get_db)):
    try:
        benchmark.record(db, body.domain, body.verdict, body.platform or None, body.notes, ctx.user.email)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"progress": benchmark.progress(db)}


@router.get("/admin/benchmark/report")
def benchmark_report(final: bool = False, ctx: Ctx = Depends(admin), db: Session = Depends(get_db)):
    """Tune split always; holdout only with ?final=true (logged)."""
    return benchmark.report(db, final=final, requested_by=ctx.user.email)


@router.get("/admin/source-rights", dependencies=[Depends(admin)])
def source_rights(db: Session = Depends(get_db)):
    from app.services import provenance

    return provenance.summary(db)
