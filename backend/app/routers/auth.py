from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import Session as UserSession, User
from ..models import utcnow
from ..security import hash_password, new_salt, new_token, verify_password

router = APIRouter(prefix="/api", tags=["auth"])

SESSION_TTL = timedelta(days=7)


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8)


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str
    username: str


class MeOut(BaseModel):
    id: int
    username: str


class OkOut(BaseModel):
    ok: bool = True


def _issue_session(session: DBSession, user: User) -> UserSession:
    now = utcnow()
    user_session = UserSession(
        token=new_token(),
        user_id=user.id,
        created_at=now,
        expires_at=now + SESSION_TTL,
    )
    session.add(user_session)
    session.commit()
    session.refresh(user_session)
    return user_session


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, session: DBSession = Depends(get_session)):
    existing = session.exec(
        select(User).where(User.username == payload.username)
    ).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="username exists")
    salt = new_salt()
    user = User(
        username=payload.username,
        pass_hash=hash_password(payload.password, salt),
        pass_salt=salt,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    user_session = _issue_session(session, user)
    return TokenOut(token=user_session.token, username=user.username)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, session: DBSession = Depends(get_session)):
    user = session.exec(
        select(User).where(User.username == payload.username)
    ).first()
    if user is None or not verify_password(payload.password, user.pass_salt, user.pass_hash):
        raise HTTPException(status_code=401, detail="bad credentials")
    user_session = _issue_session(session, user)
    return TokenOut(token=user_session.token, username=user.username)


@router.post("/logout", response_model=OkOut)
def logout(
    authorization: str | None = Header(None, alias="Authorization"),
    session: DBSession = Depends(get_session),
):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):]
        user_session = session.exec(
            select(UserSession).where(UserSession.token == token)
        ).first()
        if user_session is not None:
            session.delete(user_session)
            session.commit()
    return OkOut()


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return MeOut(id=user.id, username=user.username)