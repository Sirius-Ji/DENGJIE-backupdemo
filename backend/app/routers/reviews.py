from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import Review, Streak, Task, User, utcnow
from ..services.reschedule import reschedule_after_tired
from .goals import TaskOut, _task_to_out

router = APIRouter(prefix="/api", tags=["reviews"])


class ReviewActionIn(BaseModel):
    task_id: int
    action: str


class StreakOut(BaseModel):
    current: int
    longest: int
    last_done_date: date | None = None


class TodayTaskOut(BaseModel):
    id: int
    title: str
    estimate_hours: float
    status: str
    source: str
    goal_id: int | None = None
    deadline: date | None = None


class TodayOut(BaseModel):
    date: date
    tasks: list[TodayTaskOut]
    streak: StreakOut
    pending_count: int


class ReviewOut(BaseModel):
    id: int
    task_id: int
    action: str
    action_date: date


class RescheduleMove(BaseModel):
    task_id: int
    old_deadline: date | None = None
    new_deadline: str


class RescheduleOut(BaseModel):
    implemented: bool
    moved: list[RescheduleMove] = []
    source: str = ""
    reason: str = ""


class ReviewResultOut(BaseModel):
    task: TaskOut
    review: ReviewOut
    streak: StreakOut
    reschedule: RescheduleOut | None = None


def _get_or_create_streak(session: DBSession, user_id: int) -> Streak:
    streak = session.get(Streak, user_id)
    if streak is None:
        streak = Streak(user_id=user_id)
        session.add(streak)
        session.commit()
        session.refresh(streak)
    return streak


def _streak_out(streak: Streak) -> StreakOut:
    return StreakOut(
        current=streak.current_streak,
        longest=streak.longest_streak,
        last_done_date=streak.last_done_date,
    )


@router.get("/reviews/today", response_model=TodayOut)
def get_today(
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    today = date.today()
    tomorrow = today + timedelta(days=1)

    confirmed_q = select(Task).where(
        Task.user_id == user.id,
        Task.status == "confirmed",
    )
    confirmed_tasks = session.exec(confirmed_q).all()
    pending = [t for t in confirmed_tasks if t.deadline is None or t.deadline <= tomorrow]

    done_review_q = select(Review).where(
        Review.user_id == user.id,
        Review.action == "done",
        Review.action_date == today,
    )
    done_reviews = session.exec(done_review_q).all()
    done_task_ids = {r.task_id for r in done_reviews}
    done_tasks_q = select(Task).where(Task.id.in_(done_task_ids)) if done_task_ids else []
    done_tasks = session.exec(done_tasks_q).all() if done_task_ids else []

    tasks_out = [TodayTaskOut(
        id=t.id, title=t.title, estimate_hours=t.estimate_hours,
        status=t.status, source=t.source, goal_id=t.goal_id, deadline=t.deadline,
    ) for t in pending] + [TodayTaskOut(
        id=t.id, title=t.title, estimate_hours=t.estimate_hours,
        status=t.status, source=t.source, goal_id=t.goal_id, deadline=t.deadline,
    ) for t in done_tasks]

    streak = _get_or_create_streak(session, user.id)

    return TodayOut(
        date=today,
        tasks=tasks_out,
        streak=_streak_out(streak),
        pending_count=len(pending),
    )


@router.post("/reviews", response_model=ReviewResultOut, status_code=201)
async def create_review(
    payload: ReviewActionIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    if payload.action not in ("done", "tired", "defer"):
        raise HTTPException(status_code=400, detail="action must be done/tired/defer")

    task = session.get(Task, payload.task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="task not found")

    today = date.today()
    yesterday = today - timedelta(days=1)

    if payload.action == "done":
        if task.status != "confirmed":
            raise HTTPException(status_code=409, detail=f"cannot done task in status {task.status}")
        task.status = "done"

        streak = _get_or_create_streak(session, user.id)
        if streak.last_done_date != today:
            if streak.last_done_date == yesterday:
                streak.current_streak += 1
            else:
                streak.current_streak = 1
            streak.longest_streak = max(streak.longest_streak, streak.current_streak)
            streak.last_done_date = today
            streak.updated_at = utcnow()
            session.add(streak)

    elif payload.action in ("defer", "tired"):
        if task.deadline is not None:
            task.deadline = task.deadline + timedelta(days=1)
        else:
            task.deadline = today + timedelta(days=1)

    review = Review(
        user_id=user.id,
        task_id=task.id,
        action=payload.action,
        action_date=today,
    )
    session.add(task)
    session.add(review)
    session.commit()
    session.refresh(task)
    session.refresh(review)

    reschedule_result = None
    if payload.action == "tired":
        reschedule_result = await reschedule_after_tired(task.id, session, user.id)

    streak = _get_or_create_streak(session, user.id)

    reschedule_out = None
    if reschedule_result:
        reschedule_out = RescheduleOut(
            implemented=reschedule_result.get("implemented", False),
            moved=[RescheduleMove(
                task_id=m["task_id"],
                old_deadline=m.get("old_deadline"),
                new_deadline=m["new_deadline"],
            ) for m in reschedule_result.get("moved", [])],
            source=reschedule_result.get("source", ""),
            reason=reschedule_result.get("reason", ""),
        )

    return ReviewResultOut(
        task=_task_to_out(task),
        review=ReviewOut(
            id=review.id,
            task_id=review.task_id,
            action=review.action,
            action_date=review.action_date,
        ),
        streak=_streak_out(streak),
        reschedule=reschedule_out,
    )


@router.get("/streak", response_model=StreakOut)
def get_streak(
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    streak = _get_or_create_streak(session, user.id)
    return _streak_out(streak)