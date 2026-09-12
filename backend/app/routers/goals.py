from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import Goal, Task, User
from ..services.llm import LLMUnavailable, chat_json
from ..services.resources import all_resources, get_resource

router = APIRouter(prefix="/api", tags=["goals"])


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    deadline: date | None = None
    note: str | None = None


class GoalOut(BaseModel):
    id: int
    user_id: int
    title: str
    deadline: date | None = None
    note: str | None = None
    created_at: datetime


class TaskOut(BaseModel):
    id: int
    user_id: int
    goal_id: int | None = None
    title: str
    estimate_hours: float
    deadline: date | None = None
    source: str
    status: str
    source_ref: str | None = None
    resource_id: str | None = None
    resource_url: str | None = None
    resource_verified: bool
    dependencies: list
    created_at: datetime
    confirmed_at: datetime | None = None


class DecomposeOut(BaseModel):
    tasks: list[TaskOut]


class DecomposeTaskLLM(BaseModel):
    title: str
    estimate_hours: float = 1.0
    deadline: str | None = None
    dependencies: list[int] = []
    resource_id: str | None = None


class DecomposeLLMOut(BaseModel):
    tasks: list[DecomposeTaskLLM]


def _task_to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        user_id=task.user_id,
        goal_id=task.goal_id,
        title=task.title,
        estimate_hours=task.estimate_hours,
        deadline=task.deadline,
        source=task.source,
        status=task.status,
        source_ref=task.source_ref,
        resource_id=task.resource_id,
        resource_url=task.resource_url,
        resource_verified=task.resource_verified,
        dependencies=task.dependencies or [],
        created_at=task.created_at,
        confirmed_at=task.confirmed_at,
    )


@router.post("/goals", response_model=GoalOut, status_code=201)
def create_goal(
    payload: GoalIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    goal = Goal(user_id=user.id, title=payload.title, deadline=payload.deadline, note=payload.note)
    session.add(goal)
    session.commit()
    session.refresh(goal)
    return goal


@router.get("/goals", response_model=list[GoalOut])
def list_goals(
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    return session.exec(select(Goal).where(Goal.user_id == user.id)).all()


@router.post("/goals/{goal_id}/decompose", response_model=DecomposeOut)
async def decompose(
    goal_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    goal = session.get(Goal, goal_id)
    if goal is None or goal.user_id != user.id:
        raise HTTPException(status_code=404, detail="goal not found")

    res_list = all_resources()
    res_hint = "\n".join(f"- {r['resource_id']}: {r['title']}" for r in res_list)
    prompt = (
        f"目标：{goal.title}\n"
        f"截止日：{goal.deadline or '未指定'}\n"
        f"请拆解成 3-7 个可执行任务，每个带估时（小时）和建议截止日（YYYY-MM-DD）。\n"
        f"资源只能从以下目录选 resource_id，不要编造 URL：\n{res_hint}\n"
        f'输出 JSON: {{"tasks": [{{"title":str, "estimate_hours":float, "deadline":"YYYY-MM-DD"|null, "dependencies":[int], "resource_id":str|null}}]}}'
    )
    schema_hint = '{"tasks": [{"title": str, "estimate_hours": float, "deadline": str|null, "dependencies": [int], "resource_id": str|null}]}'

    validated: DecomposeLLMOut | None = None
    try:
        for attempt in range(2):
            try:
                result = await chat_json(prompt, schema_hint)
                result.pop("_from_cache", None)
                validated = DecomposeLLMOut.model_validate(result)
                break
            except ValidationError:
                continue
    except LLMUnavailable:
        pass

    if validated is None:
        validated = DecomposeLLMOut(tasks=[
            DecomposeTaskLLM(title=f"{goal.title} - 第1步", estimate_hours=2.0),
            DecomposeTaskLLM(title=f"{goal.title} - 第2步", estimate_hours=2.0),
        ])

    tasks_out: list[TaskOut] = []
    for t in validated.tasks:
        res = get_resource(t.resource_id)
        if res:
            resource_url = res["url"]
            resource_verified = res["verified"]
            rid = t.resource_id
        else:
            resource_url = None
            resource_verified = False
            rid = None

        deadline_date = None
        if t.deadline:
            try:
                deadline_date = date.fromisoformat(t.deadline)
            except ValueError:
                deadline_date = None

        task = Task(
            user_id=user.id,
            goal_id=goal.id,
            title=t.title,
            estimate_hours=t.estimate_hours,
            deadline=deadline_date,
            source="ai",
            status="pending_confirm",
            resource_id=rid,
            resource_url=resource_url,
            resource_verified=resource_verified,
            dependencies=t.dependencies,
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        tasks_out.append(_task_to_out(task))

    return DecomposeOut(tasks=tasks_out)