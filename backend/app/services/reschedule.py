import json
import logging
from datetime import date, timedelta

from sqlmodel import Session as DBSession, select

from ..models import Course, Task, WeeklyCapacity
from .llm import LLMUnavailable, chat_json

logger = logging.getLogger("dengjie.reschedule")


def _get_monday(d: date | None = None) -> date:
    if d is None:
        d = date.today()
    return d - timedelta(days=d.weekday())


def _daily_course_hours(courses: list[Course], weekday: int) -> float:
    total = 0.0
    for c in courses:
        if c.weekday != weekday:
            continue
        if c.weeks:
            week_num = _week_number_safe(date.today())
            if week_num not in c.weeks:
                continue
        try:
            sh, sm = map(int, c.start_time.split(":"))
            eh, em = map(int, c.end_time.split(":"))
            total += (eh * 60 + em - sh * 60 - sm) / 60.0
        except Exception:
            pass
    return round(total, 1)


def _week_number_safe(d: date) -> int:
    from ..config import settings
    if settings.term_start:
        delta = d - settings.term_start
        return delta.days // 7 + 1
    return 1


def _heuristic_fallback(tasks: list[Task], today: date) -> list[dict]:
    moves = []
    for t in tasks:
        if t.deadline is None:
            continue
        if t.deadline < today:
            continue
        if t.deadline > today + timedelta(days=14):
            continue
        new_dl = t.deadline + timedelta(days=1)
        moves.append({"task_id": t.id, "new_deadline": new_dl.isoformat()})
    return moves


async def reschedule_after_tired(
    task_id: int,
    session: DBSession,
    user_id: int,
) -> dict:
    today = date.today()
    monday = _get_monday(today)
    next_monday = monday + timedelta(days=7)

    q = select(Task).where(
        Task.user_id == user_id,
        Task.status == "confirmed",
        Task.id != task_id,
    )
    remaining = session.exec(q).all()
    schedulable = [t for t in remaining if t.deadline is not None and t.deadline >= today]
    if not schedulable:
        return {"implemented": True, "moved": [], "source": "noop", "reason": "no schedulable tasks"}

    tired_task = session.get(Task, task_id)

    cap_q = select(WeeklyCapacity).where(
        WeeklyCapacity.user_id == user_id,
        WeeklyCapacity.week_start.in_([monday, next_monday]),
    )
    caps = session.exec(cap_q).all()
    cap_this = next((c for c in caps if c.week_start == monday), None)
    cap_next = next((c for c in caps if c.week_start == next_monday), None)

    course_q = select(Course).where(Course.user_id == user_id)
    courses = session.exec(course_q).all()

    task_lines = []
    for t in schedulable:
        task_lines.append(
            f'- task_id={t.id}, title="{t.title}", estimate_hours={t.estimate_hours}, '
            f"deadline={t.deadline.isoformat()}"
        )

    daily_load = []
    for i in range(7):
        d = monday + timedelta(days=i)
        ch = _daily_course_hours(courses, i + 1)
        daily_load.append(f"{d.isoformat()}: course_hours={ch}")

    prompt = (
        f"用户标记任务「{tired_task.title if tired_task else '?'}」为太累了，需要重新安排后续任务。\n"
        f"今天：{today.isoformat()}\n"
        f"本周可用时间：{cap_this.hours if cap_this else 0} 小时\n"
        f"下周可用时间：{cap_next.hours if cap_next else 0} 小时\n\n"
        f"每日课程负载：\n" + "\n".join(daily_load) + "\n\n"
        f"需要重排的已确认任务：\n" + "\n".join(task_lines) + "\n\n"
        f"请重新分配截止日期，把任务更均匀地分散到未来可用日期。不要移动已过期任务。"
        f"新截止日期不要早于今天。返回 JSON：\n"
        f'{{"moves": [{{"task_id": 1, "new_deadline": "YYYY-MM-DD"}}], "reason": "简短说明"}}'
    )
    schema_hint = '{"moves": [{"task_id": int, "new_deadline": "YYYY-MM-DD"}], "reason": "str"}'

    moves = []
    source = "heuristic"
    reason = ""

    try:
        result = await chat_json(prompt, schema_hint)
        from_cache = result.pop("_from_cache", False)
        source = "cache" if from_cache else "llm"
        raw_moves = result.get("moves", [])
        reason = result.get("reason", "")
        valid_ids = {t.id for t in schedulable}
        for m in raw_moves:
            tid = m.get("task_id")
            nd = m.get("new_deadline")
            if tid in valid_ids and nd:
                try:
                    new_date = date.fromisoformat(nd)
                    if new_date >= today:
                        moves.append({"task_id": tid, "new_deadline": nd})
                except ValueError:
                    pass
        if not moves:
            moves = _heuristic_fallback(schedulable, today)
            source = "heuristic"
    except LLMUnavailable as e:
        logger.warning("llm unavailable for reschedule, using heuristic: %s", e)
        moves = _heuristic_fallback(schedulable, today)
        reason = f"LLM 不可用，使用启发式顺延：{e}"
    except Exception as e:
        logger.warning("reschedule llm error, using heuristic: %s", e)
        moves = _heuristic_fallback(schedulable, today)
        reason = f"LLM 异常，使用启发式顺延：{e}"

    applied = []
    for m in moves:
        tid = m["task_id"]
        nd = m["new_deadline"]
        t = session.get(Task, tid)
        if t is None or t.user_id != user_id:
            continue
        old_dl = t.deadline.isoformat() if t.deadline else None
        try:
            t.deadline = date.fromisoformat(nd)
            session.add(t)
            applied.append({"task_id": tid, "old_deadline": old_dl, "new_deadline": nd})
        except ValueError:
            pass

    if applied:
        session.commit()

    return {"implemented": True, "moved": applied, "source": source, "reason": reason}
