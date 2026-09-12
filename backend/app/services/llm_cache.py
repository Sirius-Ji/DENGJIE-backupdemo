import hashlib
import json
import logging

from sqlmodel import Session, select

from ..db import engine
from ..models import AICache, utcnow

logger = logging.getLogger("dengjie.llm_cache")


def hash_prompt(prompt: str, schema_hint: str = "") -> str:
    return hashlib.sha256((prompt + "|" + schema_hint).encode("utf-8")).hexdigest()


def get_cache(prompt_hash: str) -> dict | None:
    with Session(engine) as session:
        cache = session.exec(select(AICache).where(AICache.prompt_hash == prompt_hash)).first()
        if cache is None:
            return None
        cache.last_used_at = utcnow()
        session.add(cache)
        session.commit()
        try:
            return json.loads(cache.response)
        except (json.JSONDecodeError, ValueError):
            return None


def put_cache(prompt_hash: str, response: dict) -> None:
    with Session(engine) as session:
        existing = session.exec(select(AICache).where(AICache.prompt_hash == prompt_hash)).first()
        if existing:
            existing.response = json.dumps(response, ensure_ascii=False)
            existing.last_used_at = utcnow()
        else:
            cache = AICache(prompt_hash=prompt_hash, response=json.dumps(response, ensure_ascii=False))
            session.add(cache)
        session.commit()


def count() -> int:
    with Session(engine) as session:
        return len(session.exec(select(AICache)).all())
