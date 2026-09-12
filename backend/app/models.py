from datetime import date, datetime, timezone

from sqlalchemy import Column, Index, JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, min_length=3, max_length=32)
    pass_hash: str
    pass_salt: str
    created_at: datetime = Field(default_factory=utcnow)


class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    token: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class Goal(SQLModel, table=True):
    __tablename__ = "goals"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    title: str
    deadline: date | None = Field(default=None)
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    goal_id: int | None = Field(default=None, foreign_key="goals.id", index=True)
    title: str
    estimate_hours: float = Field(default=1.0)
    deadline: date | None = Field(default=None)
    source: str = Field(default="ai")
    status: str = Field(default="pending_confirm")
    source_ref: str | None = Field(default=None)
    resource_id: str | None = Field(default=None)
    resource_url: str | None = Field(default=None)
    resource_verified: bool = Field(default=False)
    dependencies: list | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
    confirmed_at: datetime | None = Field(default=None)
    assignee_user_id: int | None = Field(default=None, foreign_key="users.id", index=True)

    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_deadline", "user_id", "deadline"),
        Index("ix_tasks_user_goal", "user_id", "goal_id"),
    )

class WeeklyCapacity(SQLModel, table=True):
    __tablename__ = "weekly_capacity"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    week_start: date = Field(index=True)
    hours: float = Field(default=0.0)
    source: str = Field(default="manual")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "week_start", name="uq_capacity_user_week"),
    )

class Review(SQLModel, table=True):
    __tablename__ = "reviews"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    task_id: int = Field(foreign_key="tasks.id", index=True)
    action: str
    action_date: date = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)

    __table_args__ = (
        Index("ix_reviews_user_date", "user_id", "action_date"),
    )


class Streak(SQLModel, table=True):
    __tablename__ = "streaks"

    user_id: int = Field(foreign_key="users.id", primary_key=True)
    current_streak: int = Field(default=0)
    longest_streak: int = Field(default=0)
    last_done_date: date | None = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)

class Course(SQLModel, table=True):
    __tablename__ = "courses"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str
    teacher: str | None = Field(default=None)
    location: str | None = Field(default=None)
    weekday: int
    start_time: str
    end_time: str
    weeks: list | None = Field(default=None, sa_column=Column(JSON))
    source: str = Field(default="manual")
    created_at: datetime = Field(default_factory=utcnow)

    __table_args__ = (
        Index("ix_courses_user_weekday", "user_id", "weekday"),
    )

class AICache(SQLModel, table=True):
    __tablename__ = "ai_cache"

    id: int | None = Field(default=None, primary_key=True)
    prompt_hash: str = Field(unique=True, index=True)
    response: str
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)

class Group(SQLModel, table=True):
    __tablename__ = "groups"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    invite_code: str = Field(unique=True, index=True)
    invite_expires: datetime
    owner_id: int = Field(foreign_key="users.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class GroupMember(SQLModel, table=True):
    __tablename__ = "group_members"

    group_id: int = Field(foreign_key="groups.id", primary_key=True)
    user_id: int = Field(foreign_key="users.id", primary_key=True, index=True)
    joined_at: datetime = Field(default_factory=utcnow)


class GroupGoal(SQLModel, table=True):
    __tablename__ = "group_goals"

    id: int | None = Field(default=None, primary_key=True)
    group_id: int = Field(foreign_key="groups.id", index=True)
    goal_id: int = Field(foreign_key="goals.id", index=True)
    created_at: datetime = Field(default_factory=utcnow)

    __table_args__ = (
        UniqueConstraint("group_id", "goal_id", name="uq_group_goal"),
    )
