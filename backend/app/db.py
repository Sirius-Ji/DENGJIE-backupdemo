from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import settings

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)


def get_session():
    with Session(engine) as session:
        yield session


def init_db():
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT assignee_user_id FROM tasks LIMIT 1"))
            conn.rollback()
        except Exception:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN assignee_user_id INTEGER"))
            conn.commit()