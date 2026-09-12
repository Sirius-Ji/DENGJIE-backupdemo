import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session as DBSession, select

from ..db import get_session
from ..deps import get_current_user
from ..models import (
    Goal, Group, GroupGoal, GroupMember, Review, Streak, Task, User, utcnow,
)

router = APIRouter(prefix="/api", tags=["groups"])


class GroupCreateIn(BaseModel):
    name: str


class GroupCreateOut(BaseModel):
    id: int
    name: str
    invite_code: str
    invite_expires: str


class JoinIn(BaseModel):
    code: str


class JoinOut(BaseModel):
    group_id: int
    name: str


class GroupListItem(BaseModel):
    id: int
    name: str
    owner_id: int
    member_count: int
    role: str


class GroupListOut(BaseModel):
    groups: list[GroupListItem]


class MemberOut(BaseModel):
    user_id: int
    username: str
    done_count: int
    streak: int
    completion_rate: float


class GroupTaskOut(BaseModel):
    id: int
    title: str
    estimate_hours: float
    status: str
    assignee_user_id: int | None = None
    assignee_username: str | None = None


class GroupGoalProgress(BaseModel):
    total: int
    done: int
    claimed: int


class GroupGoalOut(BaseModel):
    goal_id: int
    title: str
    deadline: str | None = None
    progress: GroupGoalProgress
    tasks: list[GroupTaskOut]


class GroupDetailOut(BaseModel):
    id: int
    name: str
    owner_id: int
    invite_code: str | None = None
    members: list[MemberOut]
    goals: list[GroupGoalOut]


class AddGoalIn(BaseModel):
    goal_id: int


class AddGoalOut(BaseModel):
    group_id: int
    goal_id: int


class ClaimOut(BaseModel):
    id: int
    title: str
    estimate_hours: float
    status: str
    assignee_user_id: int | None = None


def _is_member(session: DBSession, group_id: int, user_id: int) -> bool:
    q = select(GroupMember).where(
        GroupMember.group_id == group_id,
        GroupMember.user_id == user_id,
    )
    return session.exec(q).first() is not None


@router.post("/groups", response_model=GroupCreateOut, status_code=201)
def create_group(
    payload: GroupCreateIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    code = secrets.token_hex(4)
    expires = utcnow() + timedelta(hours=24)
    group = Group(name=payload.name, invite_code=code, invite_expires=expires, owner_id=user.id)
    session.add(group)
    session.commit()
    session.refresh(group)

    member = GroupMember(group_id=group.id, user_id=user.id)
    session.add(member)
    session.commit()

    return GroupCreateOut(
        id=group.id, name=group.name, invite_code=group.invite_code,
        invite_expires=group.invite_expires.isoformat(),
    )


@router.post("/groups/join", response_model=JoinOut)
def join_group(
    payload: JoinIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Group).where(Group.invite_code == payload.code)
    group = session.exec(q).first()
    if group is None:
        raise HTTPException(status_code=404, detail="invite code not found")
    if group.invite_expires < utcnow():
        raise HTTPException(status_code=410, detail="invite code expired")
    if _is_member(session, group.id, user.id):
        raise HTTPException(status_code=409, detail="already a member")

    member = GroupMember(group_id=group.id, user_id=user.id)
    session.add(member)
    session.commit()
    return JoinOut(group_id=group.id, name=group.name)


@router.get("/groups", response_model=GroupListOut)
def list_groups(
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(GroupMember).where(GroupMember.user_id == user.id)
    memberships = session.exec(q).all()
    result: list[GroupListItem] = []
    for m in memberships:
        group = session.get(Group, m.group_id)
        if group is None:
            continue
        count_q = select(GroupMember).where(GroupMember.group_id == group.id)
        member_count = len(session.exec(count_q).all())
        role = "owner" if group.owner_id == user.id else "member"
        result.append(GroupListItem(
            id=group.id, name=group.name, owner_id=group.owner_id,
            member_count=member_count, role=role,
        ))
    return GroupListOut(groups=result)


@router.get("/groups/{group_id}", response_model=GroupDetailOut)
def get_group_detail(
    group_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    group = session.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    if not _is_member(session, group_id, user.id):
        raise HTTPException(status_code=404, detail="group not found")

    member_q = select(GroupMember).where(GroupMember.group_id == group_id)
    members = session.exec(member_q).all()

    member_outs: list[MemberOut] = []
    all_group_task_ids: list[int] = []
    for m in members:
        mu = session.get(User, m.user_id)
        done_q = select(Task).where(Task.user_id == m.user_id, Task.status == "done")
        done_count = len(session.exec(done_q).all())
        streak = session.get(Streak, m.user_id)
        streak_val = streak.current_streak if streak else 0

        gg_q = select(GroupGoal).where(GroupGoal.group_id == group_id)
        group_goals = session.exec(gg_q).all()
        total_tasks = 0
        for gg in group_goals:
            tq = select(Task).where(Task.goal_id == gg.goal_id)
            tasks = session.exec(tq).all()
            total_tasks += len(tasks)
        rate = done_count / total_tasks if total_tasks > 0 else 0.0
        member_outs.append(MemberOut(
            user_id=m.user_id, username=mu.username if mu else "?",
            done_count=done_count, streak=streak_val, completion_rate=round(rate, 4),
        ))

    gg_q = select(GroupGoal).where(GroupGoal.group_id == group_id)
    group_goals = session.exec(gg_q).all()
    goal_outs: list[GroupGoalOut] = []
    for gg in group_goals:
        goal = session.get(Goal, gg.goal_id)
        if goal is None:
            continue
        tq = select(Task).where(Task.goal_id == gg.goal_id)
        tasks = session.exec(tq).all()
        done = sum(1 for t in tasks if t.status == "done")
        claimed = sum(1 for t in tasks if t.assignee_user_id is not None)
        task_outs: list[GroupTaskOut] = []
        for t in tasks:
            assignee_name = None
            if t.assignee_user_id:
                au = session.get(User, t.assignee_user_id)
                assignee_name = au.username if au else None
            task_outs.append(GroupTaskOut(
                id=t.id, title=t.title, estimate_hours=t.estimate_hours,
                status=t.status, assignee_user_id=t.assignee_user_id,
                assignee_username=assignee_name,
            ))
        goal_outs.append(GroupGoalOut(
            goal_id=goal.id, title=goal.title,
            deadline=goal.deadline.isoformat() if goal.deadline else None,
            progress=GroupGoalProgress(total=len(tasks), done=done, claimed=claimed),
            tasks=task_outs,
        ))

    invite_code = group.invite_code if group.owner_id == user.id else None
    return GroupDetailOut(
        id=group.id, name=group.name, owner_id=group.owner_id,
        invite_code=invite_code, members=member_outs, goals=goal_outs,
    )


@router.post("/groups/{group_id}/goals", response_model=AddGoalOut, status_code=201)
def add_group_goal(
    group_id: int,
    payload: AddGoalIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    group = session.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    if not _is_member(session, group_id, user.id):
        raise HTTPException(status_code=404, detail="group not found")

    goal = session.get(Goal, payload.goal_id)
    if goal is None or goal.user_id != user.id:
        raise HTTPException(status_code=404, detail="goal not found")

    existing_q = select(GroupGoal).where(
        GroupGoal.group_id == group_id, GroupGoal.goal_id == payload.goal_id,
    )
    if session.exec(existing_q).first():
        raise HTTPException(status_code=409, detail="goal already in group")

    gg = GroupGoal(group_id=group_id, goal_id=payload.goal_id)
    session.add(gg)
    session.commit()
    session.refresh(gg)
    return AddGoalOut(group_id=group_id, goal_id=payload.goal_id)


@router.post("/groups/{group_id}/tasks/{task_id}/claim", response_model=ClaimOut)
def claim_task(
    group_id: int,
    task_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    if not _is_member(session, group_id, user.id):
        raise HTTPException(status_code=404, detail="group not found")

    gg_q = select(GroupGoal).where(GroupGoal.group_id == group_id)
    group_goals = session.exec(gg_q).all()
    group_goal_ids = {gg.goal_id for gg in group_goals}

    task = session.get(Task, task_id)
    if task is None or task.goal_id not in group_goal_ids:
        raise HTTPException(status_code=404, detail="task not found in group")
    if task.assignee_user_id is not None:
        raise HTTPException(status_code=409, detail="task already claimed")

    task.assignee_user_id = user.id
    session.add(task)
    session.commit()
    session.refresh(task)
    return ClaimOut(
        id=task.id, title=task.title, estimate_hours=task.estimate_hours,
        status=task.status, assignee_user_id=task.assignee_user_id,
    )


@router.post("/groups/{group_id}/tasks/{task_id}/unclaim", response_model=ClaimOut)
def unclaim_task(
    group_id: int,
    task_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    group = session.get(Group, group_id)
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")

    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.assignee_user_id != user.id and group.owner_id != user.id:
        raise HTTPException(status_code=403, detail="only claimer or owner can unclaim")

    task.assignee_user_id = None
    session.add(task)
    session.commit()
    session.refresh(task)
    return ClaimOut(
        id=task.id, title=task.title, estimate_hours=task.estimate_hours,
        status=task.status, assignee_user_id=task.assignee_user_id,
    )