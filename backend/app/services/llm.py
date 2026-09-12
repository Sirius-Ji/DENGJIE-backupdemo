import json
import logging

import httpx

from ..config import settings
from . import llm_cache

logger = logging.getLogger("dengjie.llm")


class LLMUnavailable(Exception):
    pass


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


async def _call_once(
    client: httpx.AsyncClient,
    base_url: str,
    api_key: str,
    payload: dict,
) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = await client.post(url, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(_strip_code_fence(content))


def _build_payload(prompt: str, schema_hint: str) -> dict:
    return {
        "model": settings.deepseek_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是任务规划助手。只返回合法 JSON，"
                    f"不要 markdown 代码块或多余文字。Schema 提示: {schema_hint}"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }


async def chat_json(prompt: str, schema_hint: str = "{}") -> dict:
    prompt_hash = llm_cache.hash_prompt(prompt, schema_hint)

    if not settings.deepseek_api_key:
        cached = llm_cache.get_cache(prompt_hash)
        if cached is not None:
            cached["_from_cache"] = True
            return cached
        raise LLMUnavailable("primary key not configured")

    payload = _build_payload(prompt, schema_hint)
    timeout = httpx.Timeout(settings.llm_timeout_seconds)
    last_err: Exception = LLMUnavailable("unknown")

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                result = await _call_once(
                    client, settings.deepseek_base_url, settings.deepseek_api_key, payload
                )
                llm_cache.put_cache(prompt_hash, result)
                return result
            except Exception as e:
                last_err = e
                logger.warning("llm primary attempt %d failed: %s", attempt + 1, type(e).__name__)

        if settings.fallback_llm_base_url and settings.fallback_llm_api_key:
            try:
                result = await _call_once(
                    client, settings.fallback_llm_base_url, settings.fallback_llm_api_key, payload
                )
                llm_cache.put_cache(prompt_hash, result)
                return result
            except Exception as e:
                last_err = e
                logger.warning("llm fallback failed: %s", type(e).__name__)

    cached = llm_cache.get_cache(prompt_hash)
    if cached is not None:
        cached["_from_cache"] = True
        return cached

    raise LLMUnavailable(str(last_err))
