from datetime import datetime

from fastapi import Depends, Header, HTTPException
from sqlmodel import Session as DBSession, select

from .db import get_session
from .models import Session as UserSession, User
from .models import utcnow


def get_current_user(
    authorization: str | None = Header(None, alias="Authorization"),
    session: DBSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="invalid auth header")
    token = authorization[len("Bearer "):]
    user_session = session.exec(
        select(UserSession).where(UserSession.token == token)
    ).first()
    if user_session is None:
        raise HTTPException(status_code=401, detail="invalid token")
    if user_session.expires_at < utcnow():
        session.delete(user_session)
        session.commit()
        raise HTTPException(status_code=401, detail="token expired")
    user = session.get(User, user_session.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return user