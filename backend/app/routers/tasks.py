from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import Task, User, utcnow
from ..services.resources import get_resource
from .goals import TaskOut, _task_to_out

router = APIRouter(prefix="/api", tags=["tasks"])


class TaskCreateIn(BaseModel):
    title: str
    estimate_hours: float = 1.0
    deadline: date | None = None
    goal_id: int | None = None


class TaskUpdateIn(BaseModel):
    title: str | None = None
    estimate_hours: float | None = None
    deadline: date | None = None
    resource_id: str | None = None
    goal_id: int | None = None


class BulkConfirmIn(BaseModel):
    ids: list[int]


class BulkConfirmOut(BaseModel):
    confirmed: list[int]
    skipped: list[int]


class TasksOut(BaseModel):
    tasks: list[TaskOut]


class OkOut(BaseModel):
    ok: bool = True


@router.get("/tasks", response_model=TasksOut)
def list_tasks(
    status: str | None = None,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Task).where(Task.user_id == user.id)
    if status:
        q = q.where(Task.status == status)
    tasks = session.exec(q).all()
    tasks.sort(key=lambda t: (t.deadline is None, t.deadline or date.max))
    return TasksOut(tasks=[_task_to_out(t) for t in tasks])


@router.post("/tasks", response_model=TaskOut, status_code=201)
def create_task(
    payload: TaskCreateIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    task = Task(
        user_id=user.id,
        goal_id=payload.goal_id,
        title=payload.title,
        estimate_hours=payload.estimate_hours,
        deadline=payload.deadline,
        source="manual",
        status="confirmed",
        confirmed_at=utcnow(),
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_to_out(task)


@router.post("/tasks/{task_id}/confirm", response_model=TaskOut)
def confirm_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    task = session.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "pending_confirm":
        raise HTTPException(status_code=409, detail=f"task status is {task.status}")
    task.status = "confirmed"
    task.confirmed_at = utcnow()
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_to_out(task)


@router.post("/tasks/bulk_confirm", response_model=BulkConfirmOut)
def bulk_confirm(
    payload: BulkConfirmIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    confirmed: list[int] = []
    skipped: list[int] = []
    for tid in payload.ids:
        task = session.get(Task, tid)
        if task is None or task.user_id != user.id or task.status != "pending_confirm":
            skipped.append(tid)
            continue
        task.status = "confirmed"
        task.confirmed_at = utcnow()
        session.add(task)
        confirmed.append(tid)
    session.commit()
    return BulkConfirmOut(confirmed=confirmed, skipped=skipped)


@router.put("/tasks/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdateIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    task = session.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status not in ("pending_confirm", "confirmed"):
        raise HTTPException(status_code=409, detail=f"cannot edit task in status {task.status}")
    if payload.title is not None:
        task.title = payload.title
    if payload.estimate_hours is not None:
        task.estimate_hours = payload.estimate_hours
    if payload.deadline is not None:
        task.deadline = payload.deadline
    if payload.goal_id is not None:
        task.goal_id = payload.goal_id
    if payload.resource_id is not None:
        res = get_resource(payload.resource_id)
        if res:
            task.resource_id = payload.resource_id
            task.resource_url = res["url"]
            task.resource_verified = res["verified"]
        else:
            task.resource_id = None
            task.resource_url = None
            task.resource_verified = False
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_to_out(task)


@router.delete("/tasks/{task_id}", response_model=OkOut)
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    task = session.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status not in ("pending_confirm", "confirmed"):
        raise HTTPException(status_code=409, detail=f"cannot delete task in status {task.status}")
    session.delete(task)
    session.commit()
    return OkOut()


@router.post("/tasks/{task_id}/done", response_model=TaskOut)
def done_task(
    task_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    task = session.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "confirmed":
        raise HTTPException(status_code=409, detail=f"cannot done task in status {task.status}")
    task.status = "done"
    session.add(task)
    session.commit()
    session.refresh(task)
    return _task_to_out(task)
