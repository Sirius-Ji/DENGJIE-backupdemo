from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import Course, Task, User, WeeklyCapacity, utcnow
from .goals import TaskOut, _task_to_out
from .courses import _week_number

router = APIRouter(prefix="/api", tags=["capacity"])


class CapacityIn(BaseModel):
    week_start: date
    hours: float


class CapacityOut(BaseModel):
    week_start: date
    hours: float
    source: str


class CapacityDetailOut(BaseModel):
    week_start: date
    week_end: date
    available_hours: float
    confirmed_hours: float
    gap_hours: float
    tasks: list[TaskOut]
    capacity_source: str


class WeekDayTask(BaseModel):
    id: int
    title: str
    estimate_hours: float
    status: str
    source: str


class WeekSlot(BaseModel):
    course_id: int
    name: str
    location: str | None = None
    start_time: str
    end_time: str


class WeekDay(BaseModel):
    date: date
    weekday: int
    tasks: list[WeekDayTask]
    slots: list[WeekSlot] = []


class WeekSummary(BaseModel):
    confirmed_hours: float
    available_hours: float
    gap_hours: float
    overloaded: bool


class WeekOut(BaseModel):
    week_start: date
    week_end: date
    days: list[WeekDay]
    unscheduled: list[WeekDayTask]
    capacity: CapacityDetailOut
    summary: WeekSummary


def _week_end(week_start: date) -> date:
    return week_start + timedelta(days=6)


def _get_confirmed_tasks(session: DBSession, user_id: int, week_start: date, week_end: date) -> list[Task]:
    q = select(Task).where(
        Task.user_id == user_id,
        Task.status == "confirmed",
    )
    tasks = session.exec(q).all()
    return [t for t in tasks if t.deadline is None or (week_start <= t.deadline <= week_end)]


def _get_capacity(session: DBSession, user_id: int, week_start: date) -> WeeklyCapacity | None:
    q = select(WeeklyCapacity).where(
        WeeklyCapacity.user_id == user_id,
        WeeklyCapacity.week_start == week_start,
    )
    return session.exec(q).first()


@router.put("/capacity", response_model=CapacityOut)
def put_capacity(
    payload: CapacityIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    cap = _get_capacity(session, user.id, payload.week_start)
    if cap is None:
        cap = WeeklyCapacity(
            user_id=user.id,
            week_start=payload.week_start,
            hours=payload.hours,
            source="manual",
        )
    else:
        cap.hours = payload.hours
        cap.source = "manual"
        cap.updated_at = utcnow()
    session.add(cap)
    session.commit()
    session.refresh(cap)
    return CapacityOut(week_start=cap.week_start, hours=cap.hours, source=cap.source)


@router.get("/capacity", response_model=CapacityDetailOut)
def get_capacity(
    week_start: date,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    week_end = _week_end(week_start)
    tasks = _get_confirmed_tasks(session, user.id, week_start, week_end)
    confirmed_hours = sum(t.estimate_hours for t in tasks)
    cap = _get_capacity(session, user.id, week_start)
    available_hours = cap.hours if cap else 0.0
    gap_hours = confirmed_hours - available_hours
    return CapacityDetailOut(
        week_start=week_start,
        week_end=week_end,
        available_hours=available_hours,
        confirmed_hours=confirmed_hours,
        gap_hours=gap_hours,
        tasks=[_task_to_out(t) for t in tasks],
        capacity_source=cap.source if cap else "manual",
    )


@router.get("/week", response_model=WeekOut)
def get_week(
    week_start: date,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    week_end = _week_end(week_start)
    tasks = _get_confirmed_tasks(session, user.id, week_start, week_end)

    week_num = _week_number(week_start)
    course_q = select(Course).where(Course.user_id == user.id)
    all_courses = session.exec(course_q).all()

    days: list[WeekDay] = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        weekday = i + 1
        day_tasks = [t for t in tasks if t.deadline == d]
        day_courses = [c for c in all_courses if c.weekday == weekday and (not c.weeks or week_num in c.weeks)]
        day_courses.sort(key=lambda c: c.start_time)
        days.append(WeekDay(
            date=d,
            weekday=weekday,
            tasks=[WeekDayTask(
                id=t.id,
                title=t.title,
                estimate_hours=t.estimate_hours,
                status=t.status,
                source=t.source,
            ) for t in day_tasks],
            slots=[WeekSlot(
                course_id=c.id,
                name=c.name,
                location=c.location,
                start_time=c.start_time,
                end_time=c.end_time,
            ) for c in day_courses],
        ))

    unscheduled = [t for t in tasks if t.deadline is None]
    unscheduled_out = [WeekDayTask(
        id=t.id,
        title=t.title,
        estimate_hours=t.estimate_hours,
        status=t.status,
        source=t.source,
    ) for t in unscheduled]

    cap = _get_capacity(session, user.id, week_start)
    available_hours = cap.hours if cap else 0.0
    confirmed_hours = sum(t.estimate_hours for t in tasks)
    gap_hours = confirmed_hours - available_hours

    capacity_detail = CapacityDetailOut(
        week_start=week_start,
        week_end=week_end,
        available_hours=available_hours,
        confirmed_hours=confirmed_hours,
        gap_hours=gap_hours,
        tasks=[_task_to_out(t) for t in tasks],
        capacity_source=cap.source if cap else "manual",
    )

    summary = WeekSummary(
        confirmed_hours=confirmed_hours,
        available_hours=available_hours,
        gap_hours=gap_hours,
        overloaded=gap_hours > 0,
    )

    return WeekOut(
        week_start=week_start,
        week_end=week_end,
        days=days,
        unscheduled=unscheduled_out,
        capacity=capacity_detail,
        summary=summary,
    )