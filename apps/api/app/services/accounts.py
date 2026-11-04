"""Accounts, tenants and sessions (M8). Standard library only.

Passwords: scrypt (N=2^14, r=8, p=1) with a per-user salt, constant-time compare.
Sessions: 32-byte random bearer tokens; only their SHA-256 is stored, so a database leak
does not leak usable sessions. Repeated failed logins lock the account for a while.
"""

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AuthSession, Membership, Organization, User

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
MAX_FAILED = 5
LOCK_MINUTES = 15


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(dk) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), dk)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def validate_credentials(email: str, password: str) -> str:
    email = email.strip().lower()
    if not EMAIL.match(email) or len(email) > 320:
        raise AuthError("enter a valid email address")
    if len(password) < 10 or len(password) > 200:
        raise AuthError("password must be 10-200 characters")
    return email


def register(db: Session, email: str, password: str, name: str | None, org_name: str | None, *, is_admin: bool = False) -> User:
    """Creates the user and their own organization (owner). The very first user becomes admin."""
    email = validate_credentials(email, password)
    first_user = db.scalar(select(func.count()).select_from(User)) == 0
    user = User(email=email, password_hash=hash_password(password), name=(name or "").strip()[:200] or None,
                is_admin=is_admin or first_user, failed_logins=0)  # fmt: skip
    org = Organization(name=(org_name or name or email.split("@")[0]).strip()[:200], plan="free")
    db.add_all([user, org])
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise AuthError("an account with this email already exists") from e
    db.add(Membership(user_id=user.id, organization_id=org.id, role="owner"))
    db.commit()
    return user


def login(db: Session, email: str, password: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        verify_password(password, hash_password("timing-equalizer"))  # same cost whether or not the user exists
        raise AuthError("invalid email or password")
    if user.locked_until and _aware(user.locked_until) > now:
        raise AuthError("too many failed attempts; try again later")
    if not user.is_active or not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED:
            user.locked_until, user.failed_logins = now + timedelta(minutes=LOCK_MINUTES), 0
        db.commit()
        raise AuthError("invalid email or password")
    user.failed_logins, user.locked_until, user.last_login_at = 0, None, now
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token), expires_at=now + timedelta(days=settings.session_days)))
    db.commit()
    return token


def logout(db: Session, token: str) -> None:
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if s:
        db.delete(s)
        db.commit()


def user_for_token(db: Session, token: str, now: datetime | None = None) -> User | None:
    now = now or datetime.now(UTC)
    s = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if s is None or _aware(s.expires_at) <= now:
        return None
    user = db.get(User, s.user_id)
    return user if user and user.is_active else None


def primary_org(db: Session, user_id: uuid.UUID) -> tuple[Organization, str]:
    """→ (organization, role). One org per user today; memberships allow more later."""
    row = db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user_id)
        .order_by(Membership.created_at)
        .limit(1)
    ).first()
    if row is None:
        raise AuthError("user has no organization")
    return row[0], row[1]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
