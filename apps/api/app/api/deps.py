"""Request authentication. Every /api/v1 route needs a valid session (Bearer token) except
register / login / the Stripe webhook; crawling and admin operations need an admin."""

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Organization, User
from app.services import accounts


@dataclass
class Ctx:
    user: User
    org: Organization
    role: str
    token: str


def bearer(authorization: str | None = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    return authorization[7:].strip()


def current(token: str = Depends(bearer), db: Session = Depends(get_db)) -> Ctx:
    user = accounts.user_for_token(db, token)
    if user is None:
        raise HTTPException(401, "session expired or invalid", headers={"WWW-Authenticate": "Bearer"})
    try:
        org, role = accounts.primary_org(db, user.id)
    except accounts.AuthError as e:
        raise HTTPException(403, str(e)) from e
    return Ctx(user, org, role, token)


def admin(ctx: Ctx = Depends(current)) -> Ctx:
    if not ctx.user.is_admin:
        raise HTTPException(403, "admin only")
    return ctx
