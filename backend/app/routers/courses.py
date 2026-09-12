import csv
import io
import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import Session as DBSession, select

from ..config import settings
from ..db import get_session
from ..deps import get_current_user
from ..models import Course, User, WeeklyCapacity, utcnow
from ..services.llm import LLMUnavailable, chat_json

router = APIRouter(prefix="/api", tags=["courses"])

BASE_HOURS = 42.0


def _term_start() -> date:
    try:
        return date.fromisoformat(settings.term_start)
    except ValueError:
        return date(2026, 9, 7)


class ManualCourseIn(BaseModel):
    name: str
    weekday: int
    start_time: str
    end_time: str
    weeks: list | None = None
    teacher: str | None = None
    location: str | None = None


class ImportIn(BaseModel):
    format: str
    content: str | None = None
    courses: list[ManualCourseIn] | None = None


class CourseOut(BaseModel):
    id: int
    name: str
    teacher: str | None = None
    location: str | None = None
    weekday: int
    start_time: str
    end_time: str
    weeks: list
    source: str


class ImportOut(BaseModel):
    added: int
    skipped: int
    courses: list[CourseOut]


class SuggestedOut(BaseModel):
    class_hours: float
    suggested_hours: float
    base: float


class AdoptIn(BaseModel):
    week_start: date


class AdoptOut(BaseModel):
    week_start: date
    hours: float
    source: str


def _course_to_out(c: Course) -> CourseOut:
    return CourseOut(
        id=c.id, name=c.name, teacher=c.teacher, location=c.location,
        weekday=c.weekday, start_time=c.start_time, end_time=c.end_time,
        weeks=c.weeks or [], source=c.source,
    )


def _parse_ics_datetime(s: str) -> datetime | None:
    try:
        if "T" in s:
            return datetime.strptime(s, "%Y%m%dT%H%M%S")
        return datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None


def parse_ics(content: str) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for line in content.splitlines():
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current:
                events.append(current)
        elif ":" in line and current is not None:
            key, value = line.split(":", 1)
            key = key.split(";")[0]
            current[key] = value

    courses: list[dict] = []
    for event in events:
        summary = event.get("SUMMARY", "未命名")
        dtstart = event.get("DTSTART", "")
        dtend = event.get("DTEND", "")
        if not dtstart or not dtend:
            continue
        start_dt = _parse_ics_datetime(dtstart)
        end_dt = _parse_ics_datetime(dtend)
        if start_dt is None or end_dt is None:
            continue

        weekday = start_dt.weekday() + 1
        start_time = start_dt.strftime("%H:%M")
        end_time = end_dt.strftime("%H:%M")

        weeks: list = []
        rrule = event.get("RRULE", "")
        if rrule:
            rrule_dict: dict[str, str] = {}
            for part in rrule.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    rrule_dict[k] = v
            if "COUNT" in rrule_dict:
                try:
                    count = int(rrule_dict["COUNT"])
                    weeks = list(range(1, count + 1))
                except ValueError:
                    pass

        courses.append({
            "name": summary,
            "weekday": weekday,
            "start_time": start_time,
            "end_time": end_time,
            "weeks": weeks,
        })
    return courses


def parse_csv(content: str) -> list[dict]:
    courses: list[dict] = []
    reader = csv.DictReader(io.StringIO(content))
    for row in reader:
        name = (row.get("name") or "").strip()
        weekday_str = (row.get("weekday") or "1").strip()
        start_time = (row.get("start_time") or "").strip()
        end_time = (row.get("end_time") or "").strip()
        if not name or not start_time or not end_time:
            continue
        try:
            weekday = int(weekday_str)
        except ValueError:
            continue
        teacher = (row.get("teacher") or "").strip() or None
        location = (row.get("location") or "").strip() or None
        weeks_str = (row.get("weeks") or "").strip()
        weeks: list = []
        if weeks_str:
            try:
                weeks = json.loads(weeks_str)
            except (json.JSONDecodeError, ValueError):
                weeks = []
        courses.append({
            "name": name, "weekday": weekday,
            "start_time": start_time, "end_time": end_time,
            "weeks": weeks, "teacher": teacher, "location": location,
        })
    return courses


def _duration_hours(start_time: str, end_time: str) -> float:
    try:
        sh, sm = map(int, start_time.split(":"))
        eh, em = map(int, end_time.split(":"))
        return (eh * 60 + em - sh * 60 - sm) / 60.0
    except (ValueError, AttributeError):
        return 0.0


def _week_number(week_start: date) -> int:
    delta = (week_start - _term_start()).days
    return delta // 7 + 1


@router.post("/courses/import", response_model=ImportOut)
def import_courses(
    payload: ImportIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    if payload.format == "manual":
        if not payload.courses:
            raise HTTPException(status_code=400, detail="courses list required for manual import")
        raw = [c.model_dump() for c in payload.courses]
    elif payload.format == "ics":
        if not payload.content:
            raise HTTPException(status_code=400, detail="content required for ics import")
        raw = parse_ics(payload.content)
    elif payload.format == "csv":
        if not payload.content:
            raise HTTPException(status_code=400, detail="content required for csv import")
        raw = parse_csv(payload.content)
    else:
        raise HTTPException(status_code=400, detail="format must be ics/csv/manual")

    added = 0
    skipped = 0
    out: list[CourseOut] = []
    for item in raw:
        try:
            weekday = int(item.get("weekday", 0))
            if not (1 <= weekday <= 7):
                skipped += 1
                continue
            name = item.get("name", "").strip()
            start_time = item.get("start_time", "").strip()
            end_time = item.get("end_time", "").strip()
            if not name or not start_time or not end_time:
                skipped += 1
                continue
            course = Course(
                user_id=user.id,
                name=name,
                teacher=item.get("teacher"),
                location=item.get("location"),
                weekday=weekday,
                start_time=start_time,
                end_time=end_time,
                weeks=item.get("weeks") or [],
                source=payload.format,
            )
            session.add(course)
            session.commit()
            session.refresh(course)
            out.append(_course_to_out(course))
            added += 1
        except Exception:
            skipped += 1
            continue

    return ImportOut(added=added, skipped=skipped, courses=out)


@router.get("/courses", response_model=list[CourseOut])
def list_courses(
    week: str | None = None,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Course).where(Course.user_id == user.id)
    q = q.order_by(Course.weekday, Course.start_time)
    courses = session.exec(q).all()
    result = [_course_to_out(c) for c in courses]
    if week:
        try:
            week_date = date.fromisoformat(week)
            week_num = _week_number(week_date)
            result = [c for c in result if not c.weeks or week_num in c.weeks]
        except ValueError:
            pass
    return result


@router.delete("/courses")
def delete_all_courses(
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Course).where(Course.user_id == user.id)
    courses = session.exec(q).all()
    count = len(courses)
    for c in courses:
        session.delete(c)
    session.commit()
    return {"deleted": count}


@router.delete("/courses/{course_id}")
def delete_one_course(
    course_id: int,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    course = session.get(Course, course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="course not found")
    session.delete(course)
    session.commit()
    return {"deleted": 1}


@router.get("/courses/suggested_hours", response_model=SuggestedOut)
def suggested_hours(
    week_start: date,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Course).where(Course.user_id == user.id)
    courses = session.exec(q).all()
    week_num = _week_number(week_start)
    total = 0.0
    for c in courses:
        weeks = c.weeks or []
        if weeks and week_num not in weeks:
            continue
        total += _duration_hours(c.start_time, c.end_time)
    suggested = max(0.0, BASE_HOURS - total)
    return SuggestedOut(class_hours=total, suggested_hours=suggested, base=BASE_HOURS)


@router.post("/capacity/adopt_suggested", response_model=AdoptOut)
def adopt_suggested(
    payload: AdoptIn,
    user: User = Depends(get_current_user),
    session: DBSession = Depends(get_session),
):
    q = select(Course).where(Course.user_id == user.id)
    courses = session.exec(q).all()
    week_num = _week_number(payload.week_start)
    total = 0.0
    for c in courses:
        weeks = c.weeks or []
        if weeks and week_num not in weeks:
            continue
        total += _duration_hours(c.start_time, c.end_time)
    suggested = max(0.0, BASE_HOURS - total)

    cap_q = select(WeeklyCapacity).where(
        WeeklyCapacity.user_id == user.id,
        WeeklyCapacity.week_start == payload.week_start,
    )
    cap = session.exec(cap_q).first()
    if cap is None:
        cap = WeeklyCapacity(
            user_id=user.id,
            week_start=payload.week_start,
            hours=suggested,
            source="schedule",
        )
    else:
        cap.hours = suggested
        cap.source = "schedule"
        cap.updated_at = utcnow()
    session.add(cap)
    session.commit()
    session.refresh(cap)
    return AdoptOut(week_start=cap.week_start, hours=cap.hours, source=cap.source)

class ParseTextIn(BaseModel):
    text: str


class ParsedCourse(BaseModel):
    name: str
    weekday: int
    start_time: str
    end_time: str
    weeks: list | None = None
    location: str | None = None


class ParsedCourseLLM(BaseModel):
    name: str
    weekday: int
    start_time: str
    end_time: str
    weeks: list | None = None
    location: str | None = None


class ParseTextLLMOut(BaseModel):
    courses: list[ParsedCourseLLM]


class ParseTextOut(BaseModel):
    courses: list[ParsedCourse]
    llm: str


@router.post("/courses/parse_text", response_model=ParseTextOut)
async def parse_text(
    payload: ParseTextIn,
    user: User = Depends(get_current_user),
):
    prompt = (
        f"你是课表解析助手。从以下课表文本中提取课程信息。\n"
        f"每门课包含：name（课程名）、weekday（1-7，1=周一）、start_time（HH:MM）、end_time（HH:MM）、weeks（周次数组如[1,2,...,16]，无法识别给null）、location（教室，无法识别给null）。\n"
        f"只返回合法 JSON，不要 markdown 代码块或多余文字。\n\n"
        f"课表文本：\n{payload.text}\n\n"
        f'输出 JSON: {{"courses": [{{"name": str, "weekday": int, "start_time": "HH:MM", "end_time": "HH:MM", "weeks": [int]|null, "location": str|null}}]}}'
    )
    schema_hint = '{"courses": [{"name": str, "weekday": int, "start_time": str, "end_time": str, "weeks": [int]|null, "location": str|null}]}'

    from pydantic import ValidationError

    validated: ParseTextLLMOut | None = None
    llm_source = "online"
    try:
        for attempt in range(2):
            try:
                result = await chat_json(prompt, schema_hint)
                from_cache = result.pop("_from_cache", False)
                if from_cache:
                    llm_source = "cache"
                validated = ParseTextLLMOut.model_validate(result)
                break
            except ValidationError:
                continue
    except LLMUnavailable:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "unavailable", "fallback": "none"},
        )

    if validated is None:
        return JSONResponse(
            status_code=503,
            content={"ok": False, "reason": "parse_failed", "fallback": "none"},
        )

    courses_out = []
    for c in validated.courses:
        if not (1 <= c.weekday <= 7):
            continue
        courses_out.append(ParsedCourse(
            name=c.name, weekday=c.weekday, start_time=c.start_time,
            end_time=c.end_time, weeks=c.weeks, location=c.location,
        ))

    return ParseTextOut(courses=courses_out, llm=llm_source)