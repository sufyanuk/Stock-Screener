"""Password hashing (bcrypt) and JWT bearer auth. Admin role gates settings & reviews."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)
SESSION_COOKIE = "si_session"
CSRF_HEADER, CSRF_VALUE = "X-Requested-With", "stockintel"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(12)).decode()


def verify_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role, "iat": now,
                       "exp": now + timedelta(minutes=s.jwt_ttl_minutes)}, s.jwt_secret, algorithm="HS256")


def owner_user(db: Session) -> User:
    """The single owner in single-user mode: the first admin if one registered earlier (so existing
    journal data stays attached), otherwise a local owner account created on first use."""
    u = db.scalars(select(User).where(User.role == "admin").order_by(User.id).limit(1)).first()
    if u:
        return u
    u = User(email="owner@local", password_hash="!", role="admin")  # "!" can never match a bcrypt hash
    db.add(u)
    try:
        db.commit()
    except IntegrityError:  # a concurrent first request created it: use that one
        db.rollback()
        u = db.scalars(select(User).where(User.email == "owner@local")).one()
    return u


def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if get_settings().single_user_mode:
        # Personal deployment: no app login. Access control is Vercel Authentication on the deployment.
        # Writes still need the custom header so other websites cannot trigger them cross-site.
        if request.method not in SAFE_METHODS and not cred and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
        return owner_user(db)
    token = cred.credentials if cred else request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    if not cred and request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        # Cookie-authenticated writes must carry a custom header, which other sites cannot send
        # without passing CORS (SameSite=Lax already blocks most cross-site requests).
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing CSRF header")
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    user = db.get(User, int(data["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return user
